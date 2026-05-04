"""YOLO label-file serializers.

Two formats:

* Detection — one line per box: ``<class> <cx> <cy> <w> <h>``.
* Segmentation — one line per ring: ``<class> x1 y1 x2 y2 ...``.
  Multi-ring polygons emit one line per ring (YOLO seg has no native
  hole concept). Bbox annotations are degraded to a 4-corner ring.

Coordinates are normalized 0..1, formatted to 6 decimal places, and
clamped — out-of-range NaN/inf values become 0 to keep downstream tools
happy.
"""
from __future__ import annotations

import math
from typing import Any


def _clamp01(n: float) -> float:
    if not math.isfinite(n):
        return 0.0
    if n < 0:
        return 0.0
    if n > 1:
        return 1.0
    return n


def _fmt(n: float) -> str:
    return f"{_clamp01(n):.6f}"


def _bbox_from_polygon(shape: dict[str, Any]) -> dict[str, float]:
    min_x = math.inf
    min_y = math.inf
    max_x = -math.inf
    max_y = -math.inf
    for ring in shape.get("rings", []):
        for p in ring:
            x, y = p["x"], p["y"]
            if x < min_x:
                min_x = x
            if y < min_y:
                min_y = y
            if x > max_x:
                max_x = x
            if y > max_y:
                max_y = y
    if not math.isfinite(min_x):
        return {"x": 0.0, "y": 0.0, "w": 0.0, "h": 0.0}
    return {
        "x": min_x,
        "y": min_y,
        "w": max_x - min_x,
        "h": max_y - min_y,
    }


def yolo_detection_file(lines: list[dict[str, Any]]) -> str:
    """``lines`` is a list of ``{classIndex, shape}`` records, where ``shape``
    is the rect/polygon shape from the annotation."""
    out: list[str] = []
    for entry in lines:
        idx = entry["classIndex"]
        shape = entry["shape"]
        if shape["kind"] == "rect":
            box = {"x": shape["x"], "y": shape["y"], "w": shape["w"], "h": shape["h"]}
        else:
            box = _bbox_from_polygon(shape)
        if box["w"] <= 0 or box["h"] <= 0:
            continue
        cx = box["x"] + box["w"] / 2
        cy = box["y"] + box["h"] / 2
        out.append(f"{idx} {_fmt(cx)} {_fmt(cy)} {_fmt(box['w'])} {_fmt(box['h'])}")
    return ("\n".join(out) + "\n") if out else ""


def _rect_as_ring(r: dict[str, Any]) -> list[dict[str, float]]:
    return [
        {"x": r["x"], "y": r["y"]},
        {"x": r["x"] + r["w"], "y": r["y"]},
        {"x": r["x"] + r["w"], "y": r["y"] + r["h"]},
        {"x": r["x"], "y": r["y"] + r["h"]},
    ]


def yolo_segmentation_file(lines: list[dict[str, Any]]) -> str:
    out: list[str] = []
    for entry in lines:
        idx = entry["classIndex"]
        shape = entry["shape"]
        if shape["kind"] == "rect":
            ring = _rect_as_ring(shape)
            if len(ring) < 3:
                continue
            coords = " ".join(f"{_fmt(p['x'])} {_fmt(p['y'])}" for p in ring)
            out.append(f"{idx} {coords}")
            continue
        for ring in shape.get("rings", []):
            if len(ring) < 3:
                continue
            coords = " ".join(f"{_fmt(p['x'])} {_fmt(p['y'])}" for p in ring)
            out.append(f"{idx} {coords}")
    return ("\n".join(out) + "\n") if out else ""
