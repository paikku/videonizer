"""FastSAM (ultralytics) backend for SAM-family model ids.

Why FastSAM for the MVP: pip-installable via `ultralytics`, ~24MB weights,
~0.5-1.5s on CPU for a 512x512 crop, supports bbox prompts. We alias all
SAM-family ids (sam, sam2, sam3) to this single backend in the registry —
the API contract is preserved while CPU inference stays interactive.

We keep ultralytics imports lazy so the rest of the app can import this
module without the dep installed (e.g. for unit tests that mock the
backend out entirely).
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

import numpy as np

from .base import BackendResult, RegionPx

logger = logging.getLogger("videonizer.segment.fastsam")


class FastSAMBackend:
    """Lazy-loading FastSAM wrapper.

    Thread-safe load(). Inference itself releases the GIL inside ultralytics'
    torch ops, so a single instance is fine for low concurrency. For higher
    fan-out the service layer can hold multiple instances.
    """

    def __init__(
        self,
        weights: str = "FastSAM-s.pt",
        weights_dir: Path | None = None,
        device: str = "cpu",
        imgsz: int = 640,
    ) -> None:
        self._weights = weights
        self._weights_dir = weights_dir
        self._device = device
        self._imgsz = imgsz
        self._model = None
        self._load_lock = threading.Lock()
        self._load_failed: str | None = None
        self.name = weights.replace(".pt", "")

    # ------------------------------------------------------------------ load
    def _resolve_weight_path(self) -> str:
        # If weights_dir is set and contains the file, prefer the local copy
        # to avoid hitting the network from a sandboxed runtime.
        if self._weights_dir is not None:
            candidate = self._weights_dir / self._weights
            if candidate.exists():
                return str(candidate)
        return self._weights

    def _load(self):
        if self._model is not None:
            return self._model
        if self._load_failed is not None:
            raise RuntimeError(self._load_failed)
        with self._load_lock:
            if self._model is not None:
                return self._model
            try:
                from ultralytics import FastSAM  # type: ignore

                weight_path = self._resolve_weight_path()
                logger.info(
                    "fastsam.load",
                    extra={"weights": weight_path, "device": self._device},
                )
                self._model = FastSAM(weight_path)
            except Exception as exc:  # noqa: BLE001 - surface any load failure
                self._load_failed = f"FastSAM load failed: {exc}"
                logger.exception("fastsam.load_failed")
                raise RuntimeError(self._load_failed) from exc
        return self._model

    def is_available(self) -> bool:
        try:
            self._load()
            return True
        except Exception:
            return False

    # --------------------------------------------------------------- infer
    def infer(
        self,
        image: np.ndarray,
        region: RegionPx,
        *,
        class_hint: str | None = None,
    ) -> BackendResult | None:
        model = self._load()

        h, w = image.shape[:2]
        # Bbox in (x1, y1, x2, y2) — what FastSAM expects.
        x1 = max(0, region.x)
        y1 = max(0, region.y)
        x2 = min(w - 1, region.x + region.w)
        y2 = min(h - 1, region.y + region.h)
        if x2 <= x1 or y2 <= y1:
            return None

        # ultralytics returns Results list-like; mask is in source-image coords.
        results = model.predict(
            source=image,
            bboxes=[[x1, y1, x2, y2]],
            imgsz=self._imgsz,
            device=self._device,
            verbose=False,
            retina_masks=True,
        )
        if not results:
            return None
        masks_obj = getattr(results[0], "masks", None)
        if masks_obj is None or masks_obj.data is None or len(masks_obj.data) == 0:
            return None

        # Take the first (best-fit) mask. Convert torch -> numpy.
        try:
            raw = masks_obj.data[0].cpu().numpy()
        except AttributeError:
            raw = np.asarray(masks_obj.data[0])
        mask = (raw > 0.5).astype(np.uint8)

        score = 1.0
        boxes = getattr(results[0], "boxes", None)
        if boxes is not None and getattr(boxes, "conf", None) is not None:
            try:
                score = float(boxes.conf[0].item())
            except Exception:
                score = 1.0

        return BackendResult(mask=mask, score=score)
