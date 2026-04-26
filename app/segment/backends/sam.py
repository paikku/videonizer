"""ultralytics SAM-class wrapper for SAM v1 / SAM 2 / MobileSAM weights.

FastSAM has its own dedicated `FastSAM` class in ultralytics; everything
else in the SAM family (mobile_sam, sam_b/l, sam2_*, sam2.1_*) is loaded
through the shared `SAM` class. This backend is parameterized on the
weight filename so the registry can hold one instance per model id with
its own pre-loaded weights.

All variants accept bbox prompts directly — no detect-then-IoU dance.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

import numpy as np

from .base import BackendResult, RegionPx

logger = logging.getLogger("videonizer.segment.sam")


class SAMBackend:
    """Backend for any SAM-style ultralytics weight (SAM v1 / SAM 2 / MobileSAM)."""

    def __init__(
        self,
        weights: str,
        weights_dir: Path | None = None,
        device: str = "cpu",
        imgsz: int = 1024,
    ) -> None:
        self._weights = weights
        self._weights_dir = weights_dir
        self._device = device
        self._imgsz = imgsz
        self._model = None
        self._load_lock = threading.Lock()
        self._load_failed: str | None = None
        self.name = weights.replace(".pt", "")

    def _resolve_weight_path(self) -> str:
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
                from ultralytics import SAM  # type: ignore

                weight_path = self._resolve_weight_path()
                logger.info(
                    "sam.load",
                    extra={"weights": weight_path, "device": self._device},
                )
                self._model = SAM(weight_path)
            except Exception as exc:  # noqa: BLE001
                self._load_failed = f"SAM load failed ({self._weights}): {exc}"
                logger.exception("sam.load_failed")
                raise RuntimeError(self._load_failed) from exc
        return self._model

    def is_available(self) -> bool:
        try:
            self._load()
            return True
        except Exception:
            return False

    def infer(
        self,
        image: np.ndarray,
        region: RegionPx,
        *,
        class_hint: str | None = None,
    ) -> BackendResult | None:
        model = self._load()

        h, w = image.shape[:2]
        x1 = max(0, region.x)
        y1 = max(0, region.y)
        x2 = min(w - 1, region.x + region.w)
        y2 = min(h - 1, region.y + region.h)
        if x2 <= x1 or y2 <= y1:
            return None

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
