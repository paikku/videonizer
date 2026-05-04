# `app/` — Service top-level

Routers, lifespan wiring, error handling, and the two top-level pipelines (normalize, segment) that predate the project/resource/image/labelset stack.

For the HTTP surface, see `API_CONTRACT.md` (root). This spec only covers the internal layout.

---

## File-by-file

| File | Role |
|---|---|
| `main.py` | FastAPI app, lifespan, CORS, exception handlers, `/v1/normalize*`, `/v1/segment*`, `/healthz`, `/metrics`. |
| `config.py` | `Settings` (pydantic-settings) — env-var-driven configuration. |
| `errors.py` | `ServiceError` family. Caught globally in `main.py` and rendered to the contract envelope. |
| `logging_conf.py` | JSON formatter for log lines. |
| `metrics.py` | Prometheus registry / counters / histograms. |
| `jobs.py` | `JobLimiter` — async semaphore + queue-length / active-count gauges for concurrency caps. |
| `normalize.py` | ffmpeg argv builder + subprocess runner + progress parser. |
| `probe.py` | ffprobe wrapper → `ProbeResult`. Decides remux vs re-encode via `is_web_compatible`. |

Subpackages: `storage/`, `routers/`, `segment/`, `export/` — each carries its own `__spec__.md`.

---

## Lifespan responsibilities (`main.py::lifespan`)

1. Read `Settings`, configure logging.
2. Create `JobLimiter`s (one for normalize, one for segment).
3. Pre-flight ffmpeg / ffprobe via `_check_binary` (`<bin> -version` exit 0). Caches the result on `app.state.{ffmpeg_ok, ffprobe_ok}` for `/healthz`.
4. Wire `app/segment/registry.py::configure_weights_dir` to the configured weights dir (defaults to repo `weights/`).
5. Optionally pre-warm segmentation backends via `SEGMENT_PRELOAD_MODELS`.

---

## Error envelope contract

Every non-2xx JSON response shipped by this service uses:

```json
{ "error": "<code>", "message": "<human readable>" }
```

Rendered by:

- `service_error_handler` for `ServiceError` (any subclass — codes mapped by class).
- `value_error_handler` for `ValueError` from `app/storage::safe_id` etc → 400 `invalid_input`.
- `request_validation_handler` for Pydantic schema failures → 422 `invalid_input`.

Don't bypass these via `HTTPException` — the contract envelope is the only shape clients parse.

---

## Module boundaries

```
app/main.py        ─uses→ app/{normalize, probe, jobs, segment, errors, metrics, logging_conf}
app/routers/*      ─uses→ app/{storage, errors, export}
app/storage/*      ─uses→ stdlib + Pillow (no HTTP types, no FastAPI)
app/segment/*      ─uses→ stdlib + numpy + Pillow + ultralytics + torch
app/export/*       ─uses→ stdlib only (zipfile)
```

Don't create cross-cuts. `app/storage` does not import `fastapi`. `app/routers` does not open files directly. `app/export` does not depend on `app/storage` (it takes a `read_image_bytes` callable).

---

## Pitfalls

- **`@app.exception_handler(ValueError)` is broad.** It maps all `ValueError`s to 400 invalid_input. Internal code that legitimately raises `ValueError` for non-input-validation reasons must catch it before it bubbles out, or this handler will leak it to clients as a 400. The current callers (`safe_id`, the `previews-on-image_batch` case) are intentional.
- **`get_settings` is `@lru_cache`d.** Tests that mutate env vars between cases must call `get_settings.cache_clear()`. The fixtures in `tests/test_*.py` already do this.
- **Routers are mounted in registration order.** `projects → resources → images → labelsets → exports`. Method dispatch (GET vs POST) means literal segments like `/tags` don't conflict with `/{iid}`, but if a new route ever uses path overlap with the same method, register the more specific one first.
