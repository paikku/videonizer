"""Thumbnail generation. PIL-based, JPEG q80, 256px max dim, RGB."""
from __future__ import annotations

import asyncio
import io

from PIL import Image, ImageOps


THUMB_MAX_DIM = 256
THUMB_QUALITY = 80


def _make_thumb_sync(data: bytes) -> bytes:
    """Decode `data`, build a 256-max thumbnail, JPEG-encode it. Sync —
    callers must run via asyncio.to_thread.
    """
    with Image.open(io.BytesIO(data)) as src:
        # Apply EXIF rotation up-front so the thumbnail orientation matches
        # what the browser shows for the original.
        src = ImageOps.exif_transpose(src)
        if src.mode != "RGB":
            src = src.convert("RGB")
        src.thumbnail((THUMB_MAX_DIM, THUMB_MAX_DIM), Image.LANCZOS)
        out = io.BytesIO()
        src.save(out, format="JPEG", quality=THUMB_QUALITY, optimize=True)
        return out.getvalue()


async def make_thumbnail(data: bytes) -> bytes:
    """Async wrapper. PIL is CPU-bound, so we offload."""
    return await asyncio.to_thread(_make_thumb_sync, data)
