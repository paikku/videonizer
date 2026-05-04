"""Path builders + the ``safe_id`` guard.

``STORAGE_ROOT`` is process-global; tests override it via
``configure_storage_root()`` and revert with ``configure_storage_root(None)``.
Production code never calls the override — the runtime root is whatever
``Settings.storage_root`` resolves to.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from ..config import get_settings

_STORAGE_ROOT_OVERRIDE: Path | None = None


def configure_storage_root(path: str | os.PathLike | None) -> None:
    """Test-only override. Pass ``None`` to revert to settings."""
    global _STORAGE_ROOT_OVERRIDE
    _STORAGE_ROOT_OVERRIDE = Path(path).resolve() if path is not None else None


def storage_root() -> Path:
    if _STORAGE_ROOT_OVERRIDE is not None:
        return _STORAGE_ROOT_OVERRIDE
    return Path(get_settings().storage_root).resolve()


# Defensive id guard: client-supplied ids are UUIDs but a malicious caller
# could try ``../etc/passwd`` to escape the storage root. Reject any id
# carrying path separators or parent-traversal sequences.
_BAD_ID_RE = re.compile(r"[\\/]|\.\.")


def safe_id(value: str) -> str:
    if not value or _BAD_ID_RE.search(value):
        raise ValueError(f"invalid id: {value!r}")
    return value


# --- path builders -----------------------------------------------------


def projects_index() -> Path:
    return storage_root() / "projects.json"


def project_dir(project_id: str) -> Path:
    return storage_root() / safe_id(project_id)


def project_file(project_id: str) -> Path:
    return project_dir(project_id) / "project.json"


def resources_index(project_id: str) -> Path:
    return project_dir(project_id) / "resources.json"


def images_index(project_id: str) -> Path:
    return project_dir(project_id) / "images.json"


def labelsets_index(project_id: str) -> Path:
    return project_dir(project_id) / "labelsets.json"


def resource_dir(project_id: str, resource_id: str) -> Path:
    return project_dir(project_id) / "resources" / safe_id(resource_id)


def image_dir(project_id: str, image_id: str) -> Path:
    return project_dir(project_id) / "images" / safe_id(image_id)


def labelset_dir(project_id: str, labelset_id: str) -> Path:
    return project_dir(project_id) / "labelsets" / safe_id(labelset_id)


def preview_path(project_id: str, resource_id: str, idx: int) -> Path:
    return resource_dir(project_id, resource_id) / "previews" / f"preview-{idx}.jpg"
