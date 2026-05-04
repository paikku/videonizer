"""Identifier and filename utilities. Pure functions, no IO."""
from __future__ import annotations

import re
from uuid import uuid4


def gen_id() -> str:
    """UUID v4. Used for projects, resources, images, label sets."""
    return str(uuid4())


_EXT_RE = re.compile(r"\.([a-zA-Z0-9]+)$")


def ext_from_name(name: str, fallback: str) -> str:
    m = _EXT_RE.search(name or "")
    return (m.group(1) if m else fallback).lower()


def mime_for_ext(ext: str) -> str:
    e = (ext or "").lower()
    if e in ("mp4", "m4v"):
        return "video/mp4"
    if e == "webm":
        return "video/webm"
    if e == "mov":
        return "video/quicktime"
    if e == "mkv":
        return "video/x-matroska"
    if e in ("jpg", "jpeg"):
        return "image/jpeg"
    if e == "png":
        return "image/png"
    if e == "webp":
        return "image/webp"
    return "application/octet-stream"
