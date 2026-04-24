"""Backend protocol shared by all CPU segmentation implementations.

All backends:
  - take an RGB ndarray (H, W, 3), uint8
  - take a region in PIXEL coordinates of that ndarray (x, y, w, h)
  - return a single binary mask (H, W) plus a confidence score in [0, 1]

Cropping, normalization, polygon conversion, etc. are the service layer's
job — keep backends as thin model wrappers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np


@dataclass(frozen=True)
class RegionPx:
    x: int
    y: int
    w: int
    h: int

    def clip_to(self, width: int, height: int) -> "RegionPx":
        x = max(0, min(width - 1, self.x))
        y = max(0, min(height - 1, self.y))
        w = max(1, min(width - x, self.w))
        h = max(1, min(height - y, self.h))
        return RegionPx(x=x, y=y, w=w, h=h)


@dataclass
class BackendResult:
    """Single-object segmentation result from a backend.

    `mask` is a 2D uint8/bool array sized to the *input* image array passed
    to `infer()`. The service layer normalizes it to image coords.
    """

    mask: np.ndarray
    score: float


class Backend(Protocol):
    """Concrete implementations live in fastsam.py / maskrcnn.py."""

    name: str  # short backend id, e.g. "fastsam-s", "maskrcnn-r50"

    def is_available(self) -> bool:
        """Return True if dependencies + weights are loadable.

        Used by the registry to short-circuit a request to 503 cleanly
        instead of crashing inside infer().
        """
        ...

    def infer(
        self,
        image: np.ndarray,
        region: RegionPx,
        *,
        class_hint: str | None = None,
    ) -> BackendResult | None:
        """Run inference. Return None when the model finds no object."""
        ...
