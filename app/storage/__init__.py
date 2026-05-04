"""Server-side persistence for projects, resources, images, label sets.

Layout under ``STORAGE_ROOT`` mirrors the contract documented in
``API_CONTRACT.md`` §0.3. This package is the only place that touches the
filesystem for persistent project data; HTTP routes import from here.

The public surface is intentionally a flat function namespace (mirroring
``vision/src/lib/server/storage.ts``) so the route layer can stay thin.
"""
from .ids import ext_from_name, gen_id, mime_for_ext
from .images import (
    bulk_tag_images,
    create_image,
    delete_image,
    get_image,
    list_images,
    read_image_bytes,
    read_image_thumb,
    update_image,
)
from .io import clear_locks
from .labelsets import (
    create_labelset,
    delete_labelset,
    get_labelset,
    get_labelset_annotations,
    list_labelsets,
    mutate_labelset,
    mutate_labelset_annotations,
    save_labelset_annotations,
    update_labelset,
)
from .paths import configure_storage_root, safe_id, storage_root
from .projects import (
    create_project,
    delete_project,
    get_project,
    get_project_summary,
    list_projects,
)
from .resources import (
    create_resource,
    delete_resource,
    get_resource,
    list_resources,
    read_preview,
    read_resource_source,
    stat_resource_source,
    update_resource,
    write_previews,
)

__all__ = [
    "clear_locks",
    "configure_storage_root",
    "storage_root",
    "safe_id",
    "gen_id",
    "ext_from_name",
    "mime_for_ext",
    "create_project",
    "delete_project",
    "get_project",
    "get_project_summary",
    "list_projects",
    "create_resource",
    "delete_resource",
    "get_resource",
    "list_resources",
    "read_preview",
    "read_resource_source",
    "stat_resource_source",
    "update_resource",
    "write_previews",
    "bulk_tag_images",
    "create_image",
    "delete_image",
    "get_image",
    "list_images",
    "read_image_bytes",
    "read_image_thumb",
    "update_image",
    "create_labelset",
    "delete_labelset",
    "get_labelset",
    "get_labelset_annotations",
    "list_labelsets",
    "mutate_labelset",
    "mutate_labelset_annotations",
    "save_labelset_annotations",
    "update_labelset",
]
