"""Binary mask -> normalized polygon rings.

Even-odd fill rule. Ring 0 is the outer boundary, the rest are holes.
All coordinates are normalized to [0, 1] against the original image size.
"""
from __future__ import annotations

from typing import Sequence

import numpy as np

# OpenCV is loaded lazily so unit tests for the API surface (validation, model
# routing) can run without the CV dep installed.
try:  # pragma: no cover - import guard
    import cv2 as _cv2
except Exception:  # pragma: no cover
    _cv2 = None


Ring = list[tuple[float, float]]


def _require_cv2():
    if _cv2 is None:
        raise RuntimeError(
            "opencv (cv2) is required for polygon extraction. "
            "Install with: pip install opencv-python-headless"
        )
    return _cv2


def mask_to_polygon(
    mask: np.ndarray,
    *,
    image_width: int,
    image_height: int,
    crop_offset: tuple[int, int] = (0, 0),
    epsilon_norm: float = 0.002,
    max_points_per_ring: int = 1000,
) -> list[Ring]:
    """Convert a binary mask into normalized polygon rings.

    Parameters
    ----------
    mask:
        2D array of any numeric dtype. Treated as binary (>0 = foreground).
        Coordinates are pixel-indexed against `crop_offset`-shifted origin.
    image_width / image_height:
        Original full-image dimensions in pixels — used for normalization.
    crop_offset:
        (x, y) offset in original-image pixels for the crop the mask was
        produced from. Use (0, 0) when the mask spans the whole frame.
    epsilon_norm:
        Douglas-Peucker tolerance in normalized image coordinates (default
        0.002 ≈ 0.2% of the longer image side). Auto-doubled until rings
        fit within `max_points_per_ring` to keep SVG render cheap.
    max_points_per_ring:
        Hard cap. If the simplified ring still exceeds this, epsilon is
        increased and the simplification re-attempted (up to 4 retries).

    Returns
    -------
    A list of rings: ring 0 is the largest outer boundary, the remaining
    rings are its direct children (holes). Empty list if no contour with
    >= 3 points is found.
    """
    cv2 = _require_cv2()

    if image_width <= 0 or image_height <= 0:
        raise ValueError("image_width and image_height must be positive")

    binary = (np.asarray(mask) > 0).astype(np.uint8)
    if binary.size == 0 or binary.max() == 0:
        return []

    contours, hierarchy = cv2.findContours(
        binary, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE
    )
    if not contours:
        return []

    # hierarchy shape: (1, N, 4) where each row = [next, prev, first_child, parent].
    # parent == -1 -> outer ring; otherwise hole of that parent.
    h = hierarchy[0] if hierarchy is not None else None

    # Pick the largest outer contour — single-object contract (§3.5).
    outer_idx = -1
    outer_area = -1.0
    for i, c in enumerate(contours):
        if h is not None and h[i][3] != -1:
            continue
        area = float(cv2.contourArea(c))
        if area > outer_area:
            outer_area = area
            outer_idx = i

    if outer_idx < 0:
        return []

    hole_indices: list[int] = []
    if h is not None:
        for i, row in enumerate(h):
            if row[3] == outer_idx:
                hole_indices.append(i)

    diag = max(image_width, image_height)
    eps_px = max(1.0, epsilon_norm * diag)

    def to_ring(contour: np.ndarray) -> Ring | None:
        # Auto-relax epsilon if simplification still exceeds cap.
        eps = eps_px
        for _ in range(5):
            approx = cv2.approxPolyDP(contour, eps, closed=True)
            if len(approx) < 3:
                return None
            if len(approx) <= max_points_per_ring:
                break
            eps *= 2
        if len(approx) < 3:
            return None
        ox, oy = crop_offset
        ring: Ring = []
        for p in approx:
            px = (float(p[0][0]) + ox) / float(image_width)
            py = (float(p[0][1]) + oy) / float(image_height)
            # Clamp into [0, 1] (§3.4 final bullet).
            ring.append((min(1.0, max(0.0, px)), min(1.0, max(0.0, py))))
        return ring

    rings: list[Ring] = []
    outer_ring = to_ring(contours[outer_idx])
    if outer_ring is None:
        return []
    rings.append(outer_ring)
    for hi in hole_indices:
        hole = to_ring(contours[hi])
        if hole is not None:
            rings.append(hole)
    return rings


def rings_aabb(rings: Sequence[Ring]) -> dict[str, float] | None:
    """Axis-aligned bounding box of the outer ring (rings[0]).

    Returns {x, y, w, h} in normalized coords, or None if empty.
    """
    if not rings or not rings[0]:
        return None
    xs = [p[0] for p in rings[0]]
    ys = [p[1] for p in rings[0]]
    x = min(xs)
    y = min(ys)
    w = max(xs) - x
    h = max(ys) - y
    return {"x": x, "y": y, "w": w, "h": h}
