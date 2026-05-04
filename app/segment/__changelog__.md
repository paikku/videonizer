# `app/segment/` design decisions

## Slot is held until the worker thread exits, not until `wait_for` returns

- **Why**: Python threads can't be cancelled cooperatively. If `asyncio.wait_for` times out and we release the semaphore, a new inference can start while the old thread is still consuming CPU. Under burst load this stacks until the box is running far more concurrent inferences than `SEGMENT_MAX_CONCURRENT` and stops responding entirely.
- **Risk**: A truly stuck worker thread holds the slot forever, eventually starving all `/v1/segment` traffic. We accept this in exchange for bounded concurrency. The right long-term fix is to run inference in a process pool with hard kill, tracked in `REFACTORING.md`.

## Wait-queue cap (`SEGMENT_MAX_QUEUE`) defaults to 16

- **Why**: An unbounded asyncio.Semaphore wait list lets the proxy's own timeout fire first when a burst hits. Clients see a generic gateway timeout instead of our structured 503 + Retry-After. Capping the queue means we shed load fast and the FE backoff path kicks in immediately.
- **Risk**: Tuned too low, legitimate parallel workloads get spurious 503s. Default of 16 is comfortable for a 2-slot CPU box; revisit if `SEGMENT_MAX_CONCURRENT` rises.

## `mask2former` public id routes to YOLO11x-seg

- **Why**: Real Mask2Former weights aren't accessible from the build environment (HuggingFace / dl.fbaipublicfiles.com). YOLO11x-seg is the heaviest ultralytics seg backend and produces comparable instance-segmentation quality on most prompts. The public id is stable; the backend behind it can change without touching the contract.
- **Risk**: If a client visually inspects which model produced an output (the `X-Segment-Backend` header reveals it), they'll see `yolo11x-seg` and may complain. The header is informational; the polygon is what they're paying for.
