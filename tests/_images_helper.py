"""Tiny helpers for image-domain tests: build a real PNG so the PIL
thumbnail step doesn't choke on synthetic bytes.
"""
from __future__ import annotations

import io

from PIL import Image as PIL_Image


def make_png(width: int = 32, height: int = 24, color: tuple = (200, 100, 50)) -> bytes:
    img = PIL_Image.new("RGB", (width, height), color=color)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def make_jpeg(width: int = 32, height: int = 24, color: tuple = (10, 200, 30)) -> bytes:
    img = PIL_Image.new("RGB", (width, height), color=color)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=90)
    return out.getvalue()
