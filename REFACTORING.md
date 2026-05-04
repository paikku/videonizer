# Deferred refactoring candidates

Things that work but could be cleaner. Each entry is a focused later-pass — don't smuggle them into feature commits. Add a `Why this matters` and a `What good looks like` so a future agent can pick one up cold.

Entries are appended; the repo has no required ordering. When you complete one, delete its entry.

---

## 1. `_read_upload_bounded` is duplicated in `app/main.py` and `app/routers/resources.py`

- **What's there**: Both files define a "read an `UploadFile` into memory while bounded by `Settings.max_upload_bytes`" helper. They're 10 lines each, identical except for an unused chunk size constant.
- **Why this matters**: A bug or a behavioral change (e.g. switch to streaming-to-disk for large uploads) has to be made twice and the two will drift.
- **What good looks like**: Move the helper to `app/uploads.py` (new module) and import from both. The module can also own `_stream_to_disk` from `main.py`.

## 2. Resource video upload is in-memory

- **What's there**: `POST /v1/projects/{id}/resources` (type=video) reads the entire upload into memory via `_read_upload_bounded`, then passes the buffer to `storage.create_resource`. Vision did the same; we kept parity. For 2 GB videos this momentarily holds 2 GB in RAM per concurrent upload.
- **Why this matters**: One concurrent multi-GB upload per worker will OOM a small box. The existing `/v1/normalize` route already streams to disk via `_stream_to_disk`; that pattern is the right one.
- **What good looks like**: `storage.create_resource` accepts a `source_path: Path` (already-on-disk file) instead of `source_buffer: bytes`. The route streams to a tmp file under `Settings.temp_dir`, hands the path to storage, storage moves the file into its final location with `os.replace`. `_stream_to_disk` from `main.py` should move alongside.

## 3. Bundle ZIP is built fully in memory

- **What's there**: `app/export/bundle.py` writes the entire archive into `io.BytesIO`, then the route returns it as `bytes`. For LabelSets with `includeImages=True` and several thousand high-res images, this can cross GB-scale.
- **Why this matters**: Same OOM exposure as #2 but on the read side. A user clicking "Download dataset" with a large `includeImages=True` LabelSet can knock a worker over.
- **What good looks like**: Stream the ZIP out via a `StreamingResponse`. `zipfile.ZipFile` accepts a file-like; combine with an `asyncio.Queue` consumed by an async generator. The validation report needs to surface before the body starts streaming, so options:
  - Headers carry `X-Validation-Warnings: <count>` and `X-Validation-Format: <format>`. Detail is dropped.
  - Or POST returns `{validation, downloadUrl}` and the actual ZIP is fetched at the URL.
  - Coordinate with the contract before changing.

## 4. Segment slot release relies on still-running threads exiting

- **What's there**: `app/main.py::_run_segment_in_slot` holds a semaphore slot until the OS thread doing inference actually exits, even after the request has surfaced 504 to the client. Python threads can't be cancelled cooperatively, so this is the only way to keep `SEGMENT_MAX_CONCURRENT` honest under timeout.
- **Why this matters**: A truly-stuck inference holds its slot indefinitely, eventually starving `/v1/segment` traffic when all slots are locked by zombie inferences.
- **What good looks like**: Move inference into a `concurrent.futures.ProcessPoolExecutor` with a hard kill on timeout. Trade-off: process startup cost on every request, plus weight loading happens per-process — addressable with a worker pool that pre-loads models.

## 5. `mask2former` public id routes to YOLO11x-seg, not real Mask2Former

- **What's there**: `app/segment/registry.py::_ROUTING` maps `mask2former` to the YOLO11x-seg backend. Real Mask2Former weights live on HuggingFace / `dl.fbaipublicfiles.com` which the build environment can't reach.
- **Why this matters**: The public id is stable across the contract, but operators who care about the backend identity (visible via `X-Segment-Backend`) will be surprised. The `models` introspection endpoint reveals the truth, so it's not hidden — but it's also not pretty.
- **What good looks like**: A real `Mask2FormerBackend` (`backends/mask2former.py`) loading `facebook/mask2former-swin-tiny-coco-instance`. ~200 MB of weights, split into 60 MB chunks like the YOLO11x-seg ones. Adds a `transformers` dependency. Coordinate with the build pipeline owner before introducing.

## 6. `STORAGE_ROOT` half-init recovery

- **What's there**: A failure mid-`create_project` (e.g. disk full while writing `images.json`) can leave a project directory with some indices written and others missing. Subsequent `list_projects` hits `get_project_summary`, which `try`/`except`s and skips the broken entry — but the directory orphans on disk and no operator alert fires.
- **Why this matters**: Disk leaks under failure scenarios, eventually a noisy "directory exists but no project.json" warrants manual cleanup.
- **What good looks like**: A startup sweep (in `lifespan`) that reconciles `projects.json` against the on-disk directories and surfaces the diff to logs/metrics. Or a stricter `create_project` that writes to a tmp directory, then atomically renames into place. Tmp-rename is safer.

## 7. Test fixtures don't share a conftest

- **What's there**: Every `tests/test_*_api.py` file repeats the same `client` fixture (configure_storage_root + clear_locks + set ffmpeg_ok / ffprobe_ok + cache_clear). Five copies, each ~12 lines.
- **Why this matters**: If one of the fixture knobs changes (new lifespan setting, new app.state field), every test file needs the edit.
- **What good looks like**: `tests/conftest.py` exporting a single `client` fixture used by all integration files. Then `test_storage.py` keeps its own lighter `storage` fixture (no TestClient).
