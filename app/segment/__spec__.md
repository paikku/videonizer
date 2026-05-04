# `app/segment/` — Image segmentation pipeline

CPU-only single-image, single-region instance segmentation. The route surface is in `API_CONTRACT.md §1`; this file documents internals.

---

## Purpose

Given an image + a normalized bbox prompt + a model id, return a polygon (rings + score). The route is `POST /v1/segment`. Five public model ids route to four CPU backends — see `registry.py::_ROUTING`.

---

## Public surface

`app/segment/__init__.py` re-exports:

- `DEFAULT_MODEL` — `"sam3"`
- `SUPPORTED_MODELS` — frozen tuple of model ids
- `segment_image(...)` — pipeline entry: parse region → crop → infer → polygon

`registry.py`:

- `resolve_backend(model_id)` — returns a `Resolved(backend, backend_id)` for the model id (lazy-loads the backend on first use).
- `configure_weights_dir(path)` — used by `app/main.py` lifespan to point backends at `/opt/segment-weights` in Docker; defaults to repo `weights/`.

---

## Backends

| public model | backend file | weight | notes |
|---|---|---|---|
| `sam3` (default) | `backends/fastsam.py` | `weights/FastSAM-s.pt` | bbox prompt, general segmentation |
| `sam2` | `backends/sam.py` (SAM 2.1) | `weights/sam2.1_t.pt` | Meta SAM 2.1 tiny |
| `sam` | `backends/sam.py` (Mobile-SAM) | `weights/mobile_sam.pt` | original SAM compatible variant |
| `mask-rcnn` | `backends/yoloseg.py` (YOLOv8n-seg) | `weights/yolov8n-seg.pt` | COCO 80, `classHint` filtering |
| `mask2former` | `backends/yoloseg.py` (YOLO11x-seg) | `weights/yolo11x-seg.pt.part_*` | reassembled at Docker build time |

All backends derive from `backends/base.py::Backend` and provide `is_available()` + `infer(image, region_px, class_hint=None)`.

---

## Pipeline (`service.py::segment_image`)

1. Parse `region` JSON → `RegionPx` in pixel coordinates of the input image.
2. Crop with `SEGMENT_CROP_PADDING` margin (default 20% of bbox extent) — saves CPU.
3. Hand off to the backend in a worker thread.
4. Convert the returned mask → polygon rings (Douglas-Peucker simplify, `SEGMENT_POLYGON_EPSILON` tolerance).
5. Re-project polygon points back into the original image's coordinate space, then normalize to `[0, 1]`.

Empty result (model didn't find an object) is a legitimate `200 {}` response — the client preserves the existing label. Don't promote this to 4xx.

---

## Concurrency / load shedding

`/v1/segment` is CPU-heavy. Three guards (in `app/main.py::_run_segment_in_slot`) keep the box from wedging under burst load:

1. **`SEGMENT_MAX_QUEUE`** — wait queue length cap. Beyond it, refuse with 503 `busy` + `Retry-After: 1` immediately (don't let the queue grow past the proxy's timeout).
2. **`SEGMENT_ACQUIRE_TIMEOUT_MS`** — slot wait budget. Refuse with 503 `busy` + `Retry-After: 2` if a slot doesn't free up.
3. **Slot-tied to the worker thread, not the awaiter** — when inference exceeds `SEGMENT_TIMEOUT_MS`, surface 504 immediately but **keep the semaphore held** until the OS thread actually exits. Python threads can't be cancelled cooperatively; releasing the slot too early would let new inferences pile on top of the still-running one and the box would silently exceed `SEGMENT_MAX_CONCURRENT`.

The contract documents which 503 / 504 the client gets in which case (`API_CONTRACT.md §1`).

---

## Pitfalls

- **`is_available()` is cheap; `infer()` may load weights.** First-request latency for an unwarmed backend is dominated by torch + ultralytics import + weight load. Use `SEGMENT_PRELOAD_MODELS` to warm at startup.
- **Crop padding affects accuracy, not just speed.** Bumping it past 0.5 starts wasting cycles on irrelevant pixels and can introduce false positives near the bbox edges. The default 0.20 is a balanced compromise.
- **Polygon ring 0 is the outer boundary; rings 1+ are holes.** `polygon.py::mask_to_rings` enforces this. The contract says `polygon[0]` is the outer ring (even-odd fill).
- **Mask2Former is currently routed to YOLO11x-seg.** A real Mask2Former backend is tracked in `REFACTORING.md`. The contract advertises `mask2former` as a stable public id; the routing change would be invisible to clients.
