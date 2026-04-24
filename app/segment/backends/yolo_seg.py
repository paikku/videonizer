"""ultralytics YOLO-seg backend for the `mask-rcnn` / `mask2former` public ids.

Picked over a real torchvision Mask R-CNN for the CPU-only MVP because:
  * YOLOv8n-seg is ~7MB vs Mask R-CNN R50 FPN v2 at ~170MB — both fit
    comfortably inside the repo without git-lfs or file splitting.
  * CPU inference is ~0.1-0.3s vs 1-2s, making it actually interactive for
    the `H` shortcut.
  * No extra framework dependency: ultralytics is already installed for
    FastSAM, and torchvision is only transitively present.

YOLO models are pure detectors — there's no bbox-prompt API. We run
inference on the region crop the service already cut out, then pick the
best candidate by (optional class filter) + IoU with the prompt bbox.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

import numpy as np

from .base import BackendResult, RegionPx

logger = logging.getLogger("videonizer.segment.yoloseg")


def _bbox_iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)
    iw = max(0.0, ix2 - ix1)
    ih = max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    a_area = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    b_area = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = a_area + b_area - inter
    return inter / union if union > 0 else 0.0


class YOLOSegBackend:
    def __init__(
        self,
        weights: str = "yolov8n-seg.pt",
        weights_dir: Path | None = None,
        device: str = "cpu",
        imgsz: int = 640,
        score_threshold: float = 0.25,
    ) -> None:
        self._weights = weights
        self._weights_dir = weights_dir
        self._device = device
        self._imgsz = imgsz
        self._score_threshold = score_threshold
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
                from ultralytics import YOLO  # type: ignore

                weight_path = self._resolve_weight_path()
                logger.info(
                    "yoloseg.load",
                    extra={"weights": weight_path, "device": self._device},
                )
                self._model = YOLO(weight_path)
            except Exception as exc:  # noqa: BLE001
                self._load_failed = f"YOLOSeg load failed: {exc}"
                logger.exception("yoloseg.load_failed")
                raise RuntimeError(self._load_failed) from exc
        return self._model

    def is_available(self) -> bool:
        try:
            self._load()
            return True
        except Exception:
            return False

    def _resolve_class_index(self, hint: str | None) -> int | None:
        if not hint:
            return None
        h = hint.strip().lower()
        names: dict[int, str] = getattr(self._model, "names", {}) or {}
        # Exact match first, then substring fallback.
        for idx, name in names.items():
            if str(name).lower() == h:
                return int(idx)
        for idx, name in names.items():
            n = str(name).lower()
            if n and (n in h or h in n):
                return int(idx)
        return None

    def infer(
        self,
        image: np.ndarray,
        region: RegionPx,
        *,
        class_hint: str | None = None,
    ) -> BackendResult | None:
        model = self._load()

        h, w = image.shape[:2]
        target_class = self._resolve_class_index(class_hint)

        predict_kwargs: dict = dict(
            source=image,
            imgsz=self._imgsz,
            device=self._device,
            verbose=False,
            conf=self._score_threshold,
            retina_masks=True,
        )
        if target_class is not None:
            predict_kwargs["classes"] = [target_class]

        results = model.predict(**predict_kwargs)
        if not results:
            return None
        res = results[0]
        masks_obj = getattr(res, "masks", None)
        if masks_obj is None or masks_obj.data is None or len(masks_obj.data) == 0:
            return None

        try:
            boxes = res.boxes.xyxy.cpu().numpy()
            scores = res.boxes.conf.cpu().numpy()
        except AttributeError:
            boxes = np.asarray(res.boxes.xyxy)
            scores = np.asarray(res.boxes.conf)

        prompt = (
            float(region.x),
            float(region.y),
            float(region.x + region.w),
            float(region.y + region.h),
        )

        best_i = -1
        best_ranked = -1.0
        for i in range(len(scores)):
            iou = _bbox_iou(tuple(boxes[i].tolist()), prompt)
            if iou <= 0:
                continue
            ranked = iou * 100 + float(scores[i])
            if ranked > best_ranked:
                best_ranked = ranked
                best_i = i

        if best_i < 0:
            return None

        try:
            raw = masks_obj.data[best_i].cpu().numpy()
        except AttributeError:
            raw = np.asarray(masks_obj.data[best_i])
        mask = (raw > 0.5).astype(np.uint8)
        return BackendResult(mask=mask, score=float(scores[best_i]))
