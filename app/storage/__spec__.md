# `app/storage/` — Persistence layer

The only module that opens files. Routes call into here; nothing else.

---

## Purpose

Persist projects, resources, images, and label sets to the local filesystem under `STORAGE_ROOT`. Provide a flat function surface (mirroring vision's TS counterpart it replaced) so HTTP routes stay thin.

---

## Public surface

Everything is async. All functions in `app/storage/__init__.py` are exported.

| Group | Functions |
|---|---|
| Projects | `list_projects`, `get_project`, `get_project_summary`, `create_project`, `delete_project` |
| Resources | `list_resources`, `get_resource`, `create_resource`, `update_resource`, `delete_resource`, `read_resource_source`, `stat_resource_source`, `write_previews`, `read_preview` |
| Images | `list_images`, `get_image`, `create_image`, `update_image`, `bulk_tag_images`, `delete_image`, `read_image_bytes`, `read_image_thumb` |
| LabelSets | `list_labelsets`, `get_labelset`, `create_labelset`, `mutate_labelset`, `update_labelset`, `delete_labelset`, `get_labelset_annotations`, `save_labelset_annotations`, `mutate_labelset_annotations` |
| Identifiers | `gen_id`, `safe_id`, `ext_from_name`, `mime_for_ext` |
| Test helpers | `configure_storage_root`, `clear_locks`, `storage_root` |

`safe_id`, `gen_id`, `ext_from_name`, `mime_for_ext` are pure (no IO). Everything else opens files.

---

## On-disk layout

```
{STORAGE_ROOT}/
  projects.json                              { "projects": [<projectId>] }
  {projectId}/
    project.json
    resources.json                           [<resourceId>]
    images.json                              [<imageId>]
    labelsets.json                           [<labelSetId>]
    resources/{resourceId}/
      meta.json
      source.<ext>                           video resources only
      previews/preview-{idx}.jpg             video resources only
    images/{imageId}/
      meta.json
      bytes.<ext>
      thumb-384.jpg                          lazily generated
    labelsets/{labelSetId}/
      meta.json
      annotations.json
```

Sub-resource indices (`resources.json` / `images.json` / `labelsets.json`) are written empty on project creation so list endpoints for a brand-new project return `[]` instead of tripping `ENOENT`.

---

## Concurrency model

Two layers protect concurrent writes.

1. **Atomic write** — `write_json` and `write_bytes_atomic` stage to a sibling tmp file in the same directory, then `os.replace` onto the target. A reader that opens the file mid-write sees either the previous bytes or the new bytes — never a partial JSON.

2. **Per-path async lock** — `with_file_lock(path, fn)` serializes RMW (read-modify-write) on the same file across concurrent requests within one process. Locks are keyed on the **resolved absolute path** so two callers reaching the file by different relative paths still serialize.

Used together: every index update (`projects.json`, sub-resource indices), every meta mutation (`update_resource`, `update_image`, `mutate_labelset`), and the lazy thumbnail encode lock the relevant file before reading + writing.

`clear_locks()` is a test-only helper. asyncio.Locks bind to the loop that first uses them, so a second test (with a fresh event loop) inheriting locks from the first would trip `RuntimeError: bound to a different event loop`. Tests call `clear_locks()` in fixture teardown.

---

## ID safety (`safe_id`)

Client-supplied ids reach `app/storage/paths.py::safe_id` before being interpolated into a filesystem path. The guard rejects anything containing `/`, `\`, or `..`. Empty strings also reject. This is the only line of defense between a malicious id and `STORAGE_ROOT/../etc/passwd`.

`safe_id` raises `ValueError`. The route layer in `app/main.py` maps `ValueError` to a 400 `invalid_input` envelope.

---

## Cascade rules

The contract requires server-enforced cascades; the frontend never issues follow-up DELETEs.

- **`delete_project(id)`** — removes the project's index entry, then `shutil.rmtree` of the project directory. Idempotent.
- **`delete_resource(projectId, resourceId)`** — calls `delete_image` for every image whose `resourceId` matches, then drops the resource index entry, then rmtree of the resource directory.
- **`delete_image(projectId, imageId)`** — for each LabelSet in the project: drop the image from `imageIds` (via `mutate_labelset`) and drop matching annotations (via `mutate_labelset_annotations`). Then drop the image index entry and rmtree the image directory.

The mutate helpers' `False`-skip semantics (return `False` from the mutator → skip write) keep cascade passes from churning timestamps on labelsets that have nothing to clean.

---

## Lazy thumbnail

`read_image_thumb` reads `thumb-384.jpg` if present; otherwise it locks the thumb path, double-checks (a parallel waiter may have written it during the wait), then encodes via Pillow:

```
ImageOps.exif_transpose() → thumbnail((384, 384), LANCZOS) → JPEG quality=75 optimize
```

The encode lock guarantees N concurrent first-access requests on the same image trigger exactly one encode.

---

## Pitfalls

- **Don't bypass the lock for "just a quick write".** A read-then-write outside `with_file_lock` will lose updates under any concurrency.
- **Don't rmtree under a lock.** The locks are per-path; rmtree walks the entire subtree. Drop the lock first; the cascade pattern in `delete_*` does this correctly.
- **`gen_id` is `uuid4`, not deterministic.** Tests that compare IDs across runs need to capture them, not hard-code.
- **`thumb-384.jpg` is keyed by the constant `_THUMB_MAX = 384`.** Changing that without a migration leaves stale thumbnails that won't be served (the path doesn't match) but also won't be regenerated (the cached file is gone from the lookup) — there'd be one slow regeneration per image on first read, and old thumbs would orphan on disk forever. If `_THUMB_MAX` ever moves, plan a sweep in the same change.
