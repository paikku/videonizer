# videonizer

FastAPI backend for the **vision** labeling workspace. Owns:

- **Project / resource / image / labelset persistence** under `STORAGE_ROOT` (`/v1/projects/...`).
- **Video normalization** — re-encodes browser-incompatible video into H.264/AAC MP4, sync or async (`/v1/normalize*`).
- **Image segmentation** — single-image, single-region instance segmentation across five public model ids (`/v1/segment*`).
- **LabelSet export** — JSON / YOLO / classify-CSV ZIP (`/v1/projects/{id}/labelsets/{lsid}/export*`).

The HTTP surface is documented in **[`API_CONTRACT.md`](./API_CONTRACT.md)** — that file is the single agreement between this service and any client. Implementation details under `app/` are not part of the contract.

---

## Prerequisites

| Tool | Version | Why |
|---|---|---|
| Python | 3.11+ | type-hint syntax, asyncio.TaskGroup |
| pip | recent | venv install |
| ffmpeg / ffprobe | 6.x or 7.x | `/v1/normalize` decodes / re-encodes video |
| git LFS or raw clone | — | the segmentation model weights ship in-repo (split into 60 MB chunks for files > 100 MB) |

Native Python only: `numpy`, `opencv-python-headless`, `Pillow`, `torch` (CPU build for prod), `ultralytics`. No GPU required — segmentation is CPU-only by design.

The Docker image bakes ffmpeg + weights so a deployment doesn't need either of the last two prerequisites at the host.

---

## Download

```bash
git clone <repo-url> videonizer
cd videonizer
```

The `weights/` directory is part of the repo. Files larger than GitHub's 100 MB cap are committed as `*.part_00 / *.part_01 / …` chunks and reassembled by the Dockerfile on build. **Do not delete or `.gitignore` the chunks** — they are the only copy of the model weights this project ships with.

If you cloned without LFS and your terminal shows tiny pointer files instead of binaries, you used the wrong clone — re-clone with the correct method (the repo does **not** use git LFS; the weights are stored as raw blobs / split chunks).

---

## Run — development

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
PORT=8000 STORAGE_ROOT=./storage ALLOWED_ORIGINS=http://localhost:3000 \
  .venv/bin/python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

- Listens at `http://127.0.0.1:8000`.
- `--reload` watches `app/` and restarts on save.
- `STORAGE_ROOT=./storage` keeps your dev data in the working tree (`./storage/`, gitignored).
- `ALLOWED_ORIGINS=http://localhost:3000` is the vision dev server. Add comma-separated origins if you run multiple frontends.

### Health check

```bash
curl http://127.0.0.1:8000/healthz
```

- `{"status":"ok"}` → ffmpeg + ffprobe are reachable.
- `503 ffmpeg_unavailable` → install ffmpeg / fix `FFMPEG_PATH`.
- `503 ffprobe_unavailable` → same for ffprobe.

If you don't need video routes for a particular debugging session, the project / resource / image / labelset / export routes don't depend on ffmpeg and will still respond — only `/v1/normalize*` requires it.

### First request

After the server is up, smoke-test the full chain:

```bash
# create a project
curl -sX POST http://127.0.0.1:8000/v1/projects \
  -H 'content-type: application/json' \
  -d '{"name":"smoke"}' | python3 -m json.tool

# list projects
curl -s http://127.0.0.1:8000/v1/projects | python3 -m json.tool
```

For the full golden-path script (project → resource → image → labelset → export), see the test suite — `tests/test_*_api.py` walks each shape end-to-end.

### Tests

```bash
.venv/bin/pytest -q                            # everything
.venv/bin/pytest tests/test_storage.py -v      # one module
.venv/bin/pytest tests/ --ignore=tests/test_segment.py
                                               # skip segment tests if torch is unavailable
```

Segment tests need `ultralytics` + `torch`. The CPU torch wheel lives on `download.pytorch.org/whl/cpu`; sandboxes that can't reach it will have to skip those tests.

---

## Run — production

### Docker (recommended)

```bash
docker build -t videonizer .
docker run --rm \
  -p 8000:8000 \
  -e ALLOWED_ORIGINS=https://your-frontend.example \
  -e STORAGE_ROOT=/data/storage \
  -v videonizer-data:/data/storage \
  videonizer
```

Image build is 2-stage: ffmpeg 7.1 binaries + their bundled libs come from `jrottenberg/ffmpeg:7.1-ubuntu`, copied into `/opt/ffmpeg/` with an `LD_LIBRARY_PATH` shim so they never shadow the base image's `libssl.so.3`. Model weights bake in from `weights/`. The runtime touches no network at startup.

The container's healthcheck (`HEALTHCHECK` directive) hits `/healthz` every 30 s — Docker / Kubernetes can read the health state directly.

### Persistent state

`STORAGE_ROOT` is the single mount point you must volume-mount. Everything project-related (`projects.json`, per-project resource bytes, image bytes, thumbnails, label sets, annotations) lives under it.

```bash
-v videonizer-data:/data/storage
```

A bind mount works equally well:

```bash
-v "$(pwd)/data:/data/storage"
```

**Do not skip the volume.** Without it the container's writable layer holds all state and a `docker rm` wipes every project.

### Reverse proxy + TLS

The recommended deployment puts videonizer behind nginx / Caddy / Traefik and terminates TLS there. Two patterns:

**Same-origin (simplest)** — proxy routes `/v1/*` to videonizer, everything else to the vision frontend. Vision builds with `NEXT_PUBLIC_VIDEONIZER_URL=""` so its bundle uses relative paths. CORS / preflight stop mattering — they're same-origin requests.

**Cross-origin** — vision and videonizer are on different domains. Set `ALLOWED_ORIGINS=https://vision.example,https://staging.vision.example` and the FastAPI middleware echoes back the matching origin per request.

### Scaling

videonizer is **single-instance only** at the moment. The persistence layer uses per-process `asyncio.Lock`s keyed on absolute file paths; two replicas pointing at the same `STORAGE_ROOT` would race each other and lose writes. If horizontal scale becomes necessary, the storage layer needs a real lock service (or a database) — tracked in [`REFACTORING.md`](./REFACTORING.md).

Vertically you can scale the worker pool — `MAX_CONCURRENT_JOBS` (normalize) and `SEGMENT_MAX_CONCURRENT` (segment) are independent caps. CPU and RAM are the bottlenecks, not file IO.

### Airgapped / mirrored builds

```bash
docker build \
  --build-arg PIP_INDEX_URL=https://pypi.your-mirror.example/simple \
  --build-arg PIP_EXTRA_INDEX_URL=https://pypi.your-mirror.example/cpu/simple \
  --build-arg PIP_TRUSTED_HOST=pypi.your-mirror.example \
  -t videonizer .
```

Internal mirror checklist:

- `torch==2.5.1` and `torchvision==0.20.1` need to be the **CPU builds** (`+cpu` wheel) — the default PyPI torch is the CUDA build, ~2 GB on disk. Either upload the CPU wheels manually or proxy `download.pytorch.org/whl/cpu`.
- `ultralytics==8.3.44` is pinned because 8.3.45 / 8.3.46 were yanked upstream; some mirrors 404 on those.
- ffmpeg / ffprobe come from the `jrottenberg/ffmpeg:7.1-ubuntu` Docker image — your registry needs that pulled.

The runtime itself never touches the network. Once the image is built, it can run in a fully-isolated environment.

### Bind-mount source for hot-reload (dev with Docker)

```bash
docker run --rm -p 8000:8000 \
  -e ALLOWED_ORIGINS=http://localhost:3000 \
  -e UVICORN_RELOAD=1 \
  -v "$(pwd)/app:/srv/app" \
  videonizer
```

`UVICORN_RELOAD=1` flips the entrypoint to `--reload --reload-dir /srv/app`. Combine with a bind mount and the container picks up source edits without rebuild. Don't use this in production — it watches the filesystem.

---

## Environment variables

| Var | Default | Purpose |
|---|---|---|
| `PORT` | `8080` (local) / `8000` (Docker) | listen port |
| `STORAGE_ROOT` | `storage` | filesystem root for persisted state |
| `ALLOWED_ORIGINS` | — | CORS whitelist (comma-separated) |
| `MAX_UPLOAD_BYTES` | `2147483648` | per-request upload cap |
| `MAX_CONCURRENT_JOBS` | CPU count | normalize concurrency cap |
| `JOB_TIMEOUT_MS` | `600000` | normalize wall-clock cap |
| `FFMPEG_PATH` / `FFPROBE_PATH` | `ffmpeg` / `ffprobe` | binary paths |
| `FFMPEG_EXTRA_ARGS` | — | shell-split args appended to the encode (NVENC/QSV/VAAPI hook) |
| `TEMP_DIR` | system default | tmp dir for normalize work |
| `LOG_LEVEL` | `INFO` | logging level |
| `SEGMENT_MAX_CONCURRENT` | `2` | segment inference slots |
| `SEGMENT_TIMEOUT_MS` | `30000` | inference wall-clock cap |
| `SEGMENT_MAX_QUEUE` | `16` | segment queue length cap (`0` disables) |
| `SEGMENT_ACQUIRE_TIMEOUT_MS` | `10000` | segment slot wait cap |
| `SEGMENT_MAX_UPLOAD_BYTES` | `16777216` | single-frame upload cap |
| `SEGMENT_CROP_PADDING` | `0.20` | bbox crop padding (fraction) |
| `SEGMENT_POLYGON_EPSILON` | `0.002` | Douglas-Peucker tolerance |
| `SEGMENT_WEIGHTS_DIR` | — / `/opt/segment-weights` (Docker) | weight dir |
| `SEGMENT_PRELOAD_MODELS` | — | comma-separated model ids to warm at startup |
| `UVICORN_RELOAD` | `0` | Docker-only flag — `1` switches the entrypoint to dev-reload mode |

---

## Operating the service

### Logs

JSON-per-line on stdout:

```json
{"ts":"...","level":"INFO","logger":"videonizer.api","msg":"normalize.done","job_id":"...","input_bytes":123,"output_bytes":456,"duration_ms":260,"input_codec":"mpeg4","input_format":"avi","remuxed":false,"success":true}
```

Common log keys:

- `normalize.done` / `normalize.fail` / `normalize.crash` — `/v1/normalize*` outcomes
- `segment.done` / `segment.timeout` / `segment.busy` / `segment.crash`
- `ffmpeg.spawn` — full argv of every ffmpeg invocation
- `binary.check_failed` — ffmpeg / ffprobe missing or linker error at startup

Pipe through `jq` for human-readable output during debugging:

```bash
docker logs -f videonizer | jq -r '. | "\(.ts) \(.level) \(.logger) \(.msg) job=\(.job_id // "-") err=\(.error // "-")"'
```

### Metrics

Prometheus exposition at `/metrics`. Counters / histograms / gauges:

- `normalize_jobs_total{outcome}` — count of normalize outcomes
- `normalize_job_duration_seconds_bucket{mode}` — duration histogram (`mode=remux|encode`)
- `normalize_input_bytes_bucket` / `normalize_output_bytes_bucket`
- `normalize_concurrent` / `normalize_queue_length` — gauges
- `segment_total{outcome,model}` / `segment_duration_seconds_bucket{model,backend}`
- `segment_concurrent` / `segment_queue_length`

Scrape interval 15 s is plenty for this workload.

### Backups

`STORAGE_ROOT` is the only state. Back it up like any directory tree:

```bash
tar czf videonizer-backup-$(date -u +%Y%m%d).tar.gz -C /data storage
```

Stop the service first if you want a guaranteed-consistent snapshot. Per-file atomic-write (`tmp + rename`) means a hot snapshot may be 1–2 files behind, but the JSON files themselves will never be torn.

### Upgrades

This service has no migration tool; the on-disk shape is documented in [`app/storage/__spec__.md`](./app/storage/__spec__.md). When the contract or storage layout changes:

1. Stop traffic / drain in-flight requests.
2. Deploy the new image.
3. Restart.

Schema-compatible changes (new optional fields, new endpoints) need no migration. Breaking changes will land under `/v2` and will document a migration path in their release notes.

---

## Acceptance gates

Live verification you should run before declaring an upgrade healthy:

With ffmpeg available:

- AVI / MKV / WMV / FLV samples each round-trip through `/v1/normalize` and play in Chrome / Safari / Firefox.
- An H.264 + AAC + MP4 + zero-rotation input takes the remux path (`X-Normalize-Remuxed: 1`) and finishes in under 5 s.
- 2 GB+ uploads return 413.
- A `.mp4`-renamed text file returns 422, not 5xx.
- A 10-minute-cap job returns 504 with no temp files left behind.

With model weights loaded:

- `POST /v1/segment` with a real frame returns a polygon, and `X-Segment-Backend` matches the routed backend in `app/segment/registry.py`.

These are pinned to manual verification because some sandbox environments can't supply ffmpeg + torch CPU wheels at the same time. Document the result in your PR — see [`.agent/definition-of-done.md`](./.agent/definition-of-done.md).

---

## Where to look next

| Concern | Doc |
|---|---|
| Public HTTP API | [`API_CONTRACT.md`](./API_CONTRACT.md) |
| Top-level service shape (lifespan, errors, mounting) | [`app/__spec__.md`](./app/__spec__.md) |
| Persistence (filesystem layout, locking, cascades) | [`app/storage/__spec__.md`](./app/storage/__spec__.md) |
| HTTP routers | [`app/routers/__spec__.md`](./app/routers/__spec__.md) |
| Segmentation pipeline | [`app/segment/__spec__.md`](./app/segment/__spec__.md) |
| Export (JSON / ZIP / validate) | [`app/export/__spec__.md`](./app/export/__spec__.md) |
| Deferred refactors | [`REFACTORING.md`](./REFACTORING.md) |
| Working in this repo | [`CLAUDE.md`](./CLAUDE.md) → `PROJECT_RULES.md`, `.agent/workflow.md` |
