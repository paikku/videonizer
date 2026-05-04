# Videonizer

FastAPI service that owns:

- **Project / resource / image / labelset persistence** under `STORAGE_ROOT` (`/v1/projects/...`).
- **Video normalization** — re-encodes browser-incompatible video into H.264/AAC MP4, sync or async (`/v1/normalize*`).
- **Image segmentation** — single-image, single-region instance segmentation across five public model ids (`/v1/segment*`).
- **LabelSet export** — JSON / YOLO / classify-CSV ZIP (`/v1/projects/{id}/labelsets/{lsid}/export*`).

The HTTP surface is documented in **[`API_CONTRACT.md`](./API_CONTRACT.md)** — that file is the single agreement between this service and any client. Implementation details under `app/` are not part of the contract.

## Where to look

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

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
PORT=8000 STORAGE_ROOT=./storage ALLOWED_ORIGINS=http://localhost:3000 \
  .venv/bin/python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

`/healthz` returns `{"status":"ok"}` once `ffmpeg` and `ffprobe` are reachable; otherwise it surfaces 503 with the offending binary in the error code.

## Environment

| Var | Default | Purpose |
|---|---|---|
| `PORT` | `8080` | listen port |
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
| `SEGMENT_WEIGHTS_DIR` | — | weight dir; the Docker image sets `/opt/segment-weights` |
| `SEGMENT_PRELOAD_MODELS` | — | comma-separated model ids to warm at startup |

## Docker

The image is a 2-stage build that copies `ffmpeg`/`ffprobe` (with `LD_LIBRARY_PATH` shim so Python keeps using the system libssl) into `python:3.12-slim`. Model weights are baked in from `./weights/` so the runtime touches no network.

```bash
docker build -t videonizer .
docker run --rm -p 8000:8000 \
  -e ALLOWED_ORIGINS=http://localhost:3000 \
  -e UVICORN_RELOAD=1 \
  -v "$(pwd)/app:/srv/app" \
  videonizer
```

For airgapped / mirrored builds, see the build args `PIP_INDEX_URL` / `PIP_EXTRA_INDEX_URL` / `PIP_TRUSTED_HOST` in the Dockerfile. CPU-only torch wheels live on `download.pytorch.org/whl/cpu` — proxy that or upload `torch==2.5.1` (CPU build) and `ultralytics==8.3.44` to your internal mirror.

## Acceptance gates

Live (with `ffmpeg` installed):

- AVI / MKV / WMV / FLV samples each round-trip through `/v1/normalize` and play in Chrome/Safari/Firefox.
- An H.264 + AAC + MP4 + zero-rotation input takes the remux path (`X-Normalize-Remuxed: 1`) and finishes in under 5 s.
- 2 GB+ uploads return 413.
- A `.mp4`-renamed text file returns 422, not 5xx.
- A 10-minute-cap job returns 504 with no temp files left behind.

Live (with model weights loaded):

- `POST /v1/segment` with a real frame returns a polygon and `X-Segment-Backend` matches the routed backend in `app/segment/registry.py`.

These are pinned to manual verification because the sandbox CI environment can't always supply ffmpeg + torch CPU wheels at the same time. Document the result in your PR — see `.agent/definition-of-done.md`.
