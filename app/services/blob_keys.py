"""Conventional blob key builders. Centralized so cascade / migration code
all agree on the layout.

Layout:
    p/<pid>/r/<rid>/source<ext>
    p/<pid>/r/<rid>/previews/<idx 4-digit>.jpg
    p/<pid>/r/<rid>/                       (cascade prefix for the resource)
    p/<pid>/i/<iid>/bytes<ext>
    p/<pid>/i/<iid>/thumb.jpg
    p/<pid>/i/<iid>/                       (cascade prefix for the image)
    p/<pid>/                               (cascade prefix for the project)
"""
from __future__ import annotations

from pathlib import PurePosixPath


def project_prefix(project_id: str) -> str:
    return f"p/{project_id}/"


def resource_prefix(project_id: str, resource_id: str) -> str:
    return f"p/{project_id}/r/{resource_id}/"


def resource_source_key(project_id: str, resource_id: str, ext: str) -> str:
    ext = ext.lstrip(".")
    suffix = f".{ext}" if ext else ""
    return f"{resource_prefix(project_id, resource_id)}source{suffix}"


def resource_preview_key(project_id: str, resource_id: str, idx: int) -> str:
    return f"{resource_prefix(project_id, resource_id)}previews/{idx:04d}.jpg"


def image_prefix(project_id: str, image_id: str) -> str:
    return f"p/{project_id}/i/{image_id}/"


def image_bytes_key(project_id: str, image_id: str, ext: str) -> str:
    ext = ext.lstrip(".")
    suffix = f".{ext}" if ext else ""
    return f"{image_prefix(project_id, image_id)}bytes{suffix}"


def image_thumb_key(project_id: str, image_id: str) -> str:
    return f"{image_prefix(project_id, image_id)}thumb.jpg"


def safe_extension(filename: str | None) -> str:
    """Extract a filename extension (without dot) safely. Empty if nothing
    sensible — never raises.
    """
    if not filename:
        return ""
    ext = PurePosixPath(filename).suffix.lstrip(".").lower()
    # Cap length and allow only a small charset; prevents path tricks.
    ext = "".join(c for c in ext if c.isalnum())
    return ext[:8]
