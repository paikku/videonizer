"""Top-level orchestration for /v1/segment.

  segment_image(file_bytes, region, model, class_hint)
    -> SegmentResult { polygon, rect, score, backend_id }

The route layer in `app.main` wraps this in semaphore + thread offload
(model inference is sync + GIL-light enough to ship to a worker thread).
"""
from __future__ import annotations

import io
import json
import logging
from dataclasses import dataclass
from typing import Callable

import numpy as np

from ..errors import (
    SegmentImageDecodeFailed,
    SegmentInvalidRegion,
    SegmentUnsupportedImage,
    SegmentUnsupportedModel,
)
from .backends.base import RegionPx
from .polygon import Ring, mask_to_polygon, rings_aabb
from .registry import (
    DEFAULT_MODEL,
    SUPPORTED_MODELS,
    ResolvedBackend,
    resolve_backend,
)

logger = logging.getLogger("videonizer.segment")


@dataclass
class SegmentResult:
    polygon: list[Ring]
    rect: dict[str, float] | None
    score: float | None
    backend_id: str

    def to_response(self) -> dict:
        body: dict = {
            "polygon": [[list(p) for p in ring] for ring in self.polygon],
        }
        if self.rect is not None:
            body["rect"] = self.rect
        if self.score is not None:
            body["score"] = self.score
        return body


# --- input parsing -----------------------------------------------------------


def parse_region(raw: str) -> tuple[float, float, float, float]:
    """Parse the `region` form field. Returns (x, y, w, h) in normalized coords."""
    if not raw:
        raise SegmentInvalidRegion("region is required")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SegmentInvalidRegion(f"region is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SegmentInvalidRegion("region must be a JSON object")
    try:
        x = float(data["x"])
        y = float(data["y"])
        w = float(data["w"])
        h = float(data["h"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SegmentInvalidRegion(
            "region must have numeric x, y, w, h fields"
        ) from exc

    if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
        raise SegmentInvalidRegion("region x/y must be in [0, 1]")
    if not (w > 0 and h > 0):
        raise SegmentInvalidRegion("region w/h must be > 0")
    if x + w > 1.0 + 1e-6 or y + h > 1.0 + 1e-6:
        raise SegmentInvalidRegion("region exceeds image bounds")
    # Clamp tiny float overshoot.
    w = min(w, 1.0 - x)
    h = min(h, 1.0 - y)
    return x, y, w, h


def validate_model(model: str | None) -> str:
    name = (model or DEFAULT_MODEL).strip()
    if name not in SUPPORTED_MODELS:
        raise SegmentUnsupportedModel(name)
    return name


def decode_image(data: bytes) -> np.ndarray:
    """Decode bytes -> RGB ndarray (H, W, 3) uint8.

    PIL is required (already a transitive dep of torchvision). We accept what
    PIL accepts and reject anything else with 415.
    """
    if not data:
        raise SegmentImageDecodeFailed("empty image payload")
    try:
        from PIL import Image, UnidentifiedImageError  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "Pillow is required for image decoding. pip install pillow"
        ) from exc

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except UnidentifiedImageError as exc:
        raise SegmentUnsupportedImage(f"unrecognized image format: {exc}") from exc
    except Exception as exc:
        raise SegmentImageDecodeFailed(f"image decode failed: {exc}") from exc

    if img.mode != "RGB":
        img = img.convert("RGB")
    return np.asarray(img)


# --- crop helper -------------------------------------------------------------


def _crop_region(
    image: np.ndarray,
    region_norm: tuple[float, float, float, float],
    *,
    padding: float,
) -> tuple[np.ndarray, tuple[int, int]]:
    """Crop the image to a padded region. Return (cropped, (offset_x, offset_y))."""
    h, w = image.shape[:2]
    x, y, rw, rh = region_norm
    pad_w = rw * padding
    pad_h = rh * padding
    x0 = max(0.0, x - pad_w)
    y0 = max(0.0, y - pad_h)
    x1 = min(1.0, x + rw + pad_w)
    y1 = min(1.0, y + rh + pad_h)
    px0 = int(round(x0 * w))
    py0 = int(round(y0 * h))
    px1 = max(px0 + 1, int(round(x1 * w)))
    py1 = max(py0 + 1, int(round(y1 * h)))
    cropped = image[py0:py1, px0:px1]
    return cropped, (px0, py0)


# --- main entrypoint ---------------------------------------------------------


def segment_image(
    file_bytes: bytes,
    region_raw: str,
    model: str | None,
    class_hint: str | None = None,
    *,
    crop_padding: float = 0.20,
    epsilon_norm: float = 0.002,
    resolve: Callable[[str], ResolvedBackend] = resolve_backend,
) -> SegmentResult | None:
    """Synchronous end-to-end pipeline. Returns None when the model finds no object.

    `resolve` is injectable so tests can swap in a fake backend without
    touching the global registry.
    """
    model_id = validate_model(model)
    region_norm = parse_region(region_raw)
    image = decode_image(file_bytes)

    full_h, full_w = image.shape[:2]
    if full_w == 0 or full_h == 0:
        raise SegmentImageDecodeFailed("decoded image has zero size")

    # Crop-then-segment (proposal §2-a) — drives the bulk of CPU speedup.
    cropped, (off_x, off_y) = _crop_region(image, region_norm, padding=crop_padding)
    crop_h, crop_w = cropped.shape[:2]

    # Region in cropped-image pixel coords (the bbox prompt for the backend).
    rx, ry, rw, rh = region_norm
    region_px = RegionPx(
        x=int(round(rx * full_w)) - off_x,
        y=int(round(ry * full_h)) - off_y,
        w=max(1, int(round(rw * full_w))),
        h=max(1, int(round(rh * full_h))),
    ).clip_to(crop_w, crop_h)

    resolved: ResolvedBackend = resolve(model_id)

    out = resolved.backend.infer(cropped, region_px, class_hint=class_hint)
    if out is None or out.mask is None:
        logger.info(
            "segment.no_object",
            extra={"model": model_id, "backend": resolved.backend_id},
        )
        return None

    rings = mask_to_polygon(
        out.mask,
        image_width=full_w,
        image_height=full_h,
        crop_offset=(off_x, off_y),
        epsilon_norm=epsilon_norm,
    )
    if not rings:
        logger.info(
            "segment.empty_polygon",
            extra={"model": model_id, "backend": resolved.backend_id},
        )
        return None

    rect = rings_aabb(rings)
    return SegmentResult(
        polygon=rings,
        rect=rect,
        score=float(out.score) if out.score is not None else None,
        backend_id=resolved.backend_id,
    )
