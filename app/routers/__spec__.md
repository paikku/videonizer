# `app/routers/` — HTTP routers

Thin adapters between FastAPI's request shape and `app/storage`. One module per resource (projects, resources, images, labelsets, exports). Mounted in `app/main.py` after the existing `/v1/normalize` and `/v1/segment` routes.

---

## Purpose

Translate request bodies into storage calls, translate storage results back into the response envelopes documented in `API_CONTRACT.md`. **Don't** duplicate validation, locking, or cascade logic that already lives in `storage`.

---

## Public surface

Each router file exports a single `router: APIRouter`. `main.py` mounts it via `app.include_router`. Adding a new router means: write the file, import it in `main.py`, call `app.include_router(...)`.

| Module | Prefix | Notes |
|---|---|---|
| `projects.py` | `/v1/projects` | List / create / get / delete. |
| `resources.py` | `/v1/projects/{project_id}/resources` | CRUD + `/source` (Range-aware streaming) + `/previews/{idx}` + child `/images` ingest. |
| `images.py` | `/v1/projects/{project_id}/images` | CRUD + `/bytes` + `/thumb` + `/tags` (bulk). |
| `labelsets.py` | `/v1/projects/{project_id}/labelsets` | CRUD + `/annotations` (full replace). |
| `exports.py` | `/v1/projects/{project_id}/labelsets/{lsid}/export` | JSON dump, ZIP dataset, validate dry-run. |

`exports.py` lives next to `labelsets.py` instead of inside it because the export pipeline pulls in `app/export/*` and the file is already long.

---

## Error mapping

The exception handlers live in `app/main.py`:

- `ServiceError` → JSON `{ error, message }` at `exc.status`. All custom errors derive from this.
- `ValueError` → 400 `invalid_input`. Storage's `safe_id` raises `ValueError` for path-traversal attempts; this handler routes that to a clean envelope.
- `RequestValidationError` (Pydantic) → 422 `invalid_input`. Overrides FastAPI's default `{detail: [...]}` shape so every non-2xx response across the API uses the contract envelope.

Routers should raise `NotFoundError` / `BadRequestError` (defined in `app/errors.py`) for the common cases. Don't reach for `HTTPException` — it bypasses the contract envelope.

A few cases need a one-off `ServiceError`:
- `POST /resources/{rid}/previews` against an `image_batch` resource → 422 `invalid_input` (semantic mismatch, not a path/typo).

---

## Upload helpers

`resources.py` defines `_read_upload_bounded(file, limit)` for in-memory image / preview / video uploads, mirroring the inlined helper in `app/main.py` used for `/v1/normalize` and `/v1/segment`. Bound is `Settings.max_upload_bytes`. We could lift this to a shared module but the duplication is two short functions and not worth a new module yet — see `REFACTORING.md`.

Source bytes for the existing `/v1/normalize` route still go through `_stream_to_disk` (in `main.py`) since they're typically GB-scale. The new resource video upload uses `_read_upload_bounded` for parity with vision's prior in-memory path; that's a wart called out in `REFACTORING.md`.

---

## Range-streaming

`/v1/projects/{id}/resources/{rid}/source` is the only route that does HTTP byte-range. The handler:

1. Calls `storage.stat_resource_source` for `(path, size, ext)`.
2. With no `Range` header, returns 200 + `Accept-Ranges: bytes` + full body via `StreamingResponse` (yields 64 KiB chunks).
3. With `Range: bytes=START-END` (or suffix `bytes=-N`, or open-ended `bytes=START-`), parses, validates, returns 206 + `Content-Range`.
4. Malformed or out-of-bounds → 416 + `Content-Range: bytes */<total>`.

The streamer reads from a regular `open(path, "rb")` inside the async generator. For multi-GB videos this is fine because the OS does the page caching; we never load the whole file into Python.

---

## Pitfalls

- **`POST /v1/projects/{id}/images/tags` is a sibling of `GET /{iid}` etc.** Route registration order doesn't matter for HTTP method dispatch (POST vs GET), but be careful when adding new `/{iid}/...` style child routes — keep `/tags` distinct or add a `/{iid}` regex constraint.
- **Don't catch `Exception` in routers.** Exception classes in `app/errors.py` are the contract; an `except Exception` swallows the structured error and returns 500 `internal_error`, which clients can't disambiguate.
- **`PATCH` is partial; `PUT /annotations` is full replace.** This is in the contract and in the storage signatures — don't "improve" `PUT` to merge by id without a contract revision.
