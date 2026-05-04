"""Aggregate import for all ORM model modules.

Each PR that introduces a new entity adds its module here so that:
- ``Base.metadata`` sees every table (alembic autogenerate)
- a single ``import app.storage.models`` is enough at app/test boot

PR #1 ships an empty baseline; subsequent PRs extend.
"""
from __future__ import annotations

from .repo.projects import Project  # noqa: F401
from .repo.resources import Resource  # noqa: F401
# PR #4: from .repo.images import Image  # noqa: F401
# PR #5: from .repo.labelsets import LabelSet  # noqa: F401
# PR #6: from .repo.annotations import Annotation  # noqa: F401
