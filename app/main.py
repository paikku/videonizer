from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from dataclasses import dataclass, field
from typing import AsyncIterator, Literal

from fastapi import FastAPI, Form, Header, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from . import metrics
from .config import Settings, get_settings
from .errors import (
    NormalizeError,
    SegmentBusy,
    SegmentError,
    ServiceError,
    UploadTooLarge,
)
from .jobs import JobLimiter
from .logging_conf import configure_logging
from .normalize import iter_file, normalize_file
from .routers import images as images_router
from .routers import projects as projects_router
from .routers import resources as resources_router
from .segment import (
    DEFAULT_MODEL as SEGMENT_DEFAULT_MODEL,
    SUPPORTED_MODELS as SEGMENT_SUPPORTED_MODELS,
    segment_image,
)
from .segment.registry import configure_weights_dir, resolve_backend

logger = logging.getLogger("videonizer.api")

UPLOAD_CHUNK = 1024 * 1024  # 1 MiB


@dataclass
class NormalizeJob:
    id: str
    status: Literal["queued", "processing", "done", "failed"]
    progress: float = 0.0
    message: str | None = None
    output_path: Path | None = None
    work_dir: Path | None = None
    headers: dict[str, str] = field(default_factory=dict)
    created_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    app.state.settings = settings
    app.state.limiter = JobLimiter(settings.max_concurrent_jobs)
    app.state.segment_limiter = JobLimiter(settings.segment_max_concurrent)
    app.state.jobs: dict[str, NormalizeJob] = {}
    app.state.jobs_lock = asyncio.Lock()
    app.state.ffmpeg_ok = await _check_binary(settings.ffmpeg_path)
    app.state.ffprobe_ok = await _check_binary(settings.ffprobe_path)

    # Wire the segmentation registry to the configured weights dir (if any),
    # then optionally pre-warm requested backends so the first request doesn't
    # eat the load latency.
    configure_weights_dir(settings.segment_weights_dir or None)
    for model_id in settings.segment_preload_models_list:
        if model_id not in SEGMENT_SUPPORTED_MODELS:
            logger.warning("segment.preload.unknown_model", extra={"model": model_id})
            continue
        try:
            resolved = resolve_backend(model_id)
            ok = await asyncio.to_thread(resolved.backend.is_available)
            logger.info(
                "segment.preload",
                extra={"model": model_id, "backend": resolved.backend_id, "ok": ok},
            )
        except Exception:  # noqa: BLE001
            logger.exception("segment.preload.failed", extra={"model": model_id})

    logger.info(
        "startup",
        extra={
            "max_concurrent_jobs": settings.max_concurrent_jobs,
            "max_upload_bytes": settings.max_upload_bytes,
            "job_timeout_ms": settings.job_timeout_ms,
            "ffmpeg_ok": app.state.ffmpeg_ok,
            "ffprobe_ok": app.state.ffprobe_ok,
            "segment_max_concurrent": settings.segment_max_concurrent,
        },
    )
    yield


app = FastAPI(title="Videonizer Normalize Service", version="0.1.0", lifespan=lifespan)

# Mount the project / resource / image / labelset routers below the
# normalize and segment routes that live directly on `app`.
app.include_router(projects_router.router)
app.include_router(resources_router.router)
app.include_router(images_router.router)


# CORS ------------------------------------------------------------------------

_settings_boot = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings_boot.allowed_origins_list or [],
    allow_credentials=False,
    # Project / image / labelset routes use PATCH, PUT, DELETE in addition
    # to the existing GET/POST surface.
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=[
        "X-Normalize-Duration-Ms",
        "X-Normalize-Input-Codec",
        "X-Normalize-Remuxed",
        "X-Segment-Backend",
        "X-Segment-Duration-Ms",
        "Content-Length",
    ],
)


# Error handling --------------------------------------------------------------


@app.exception_handler(ServiceError)
async def service_error_handler(_: Request, exc: ServiceError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content={"error": exc.code, "message": exc.message},
    )


@app.exception_handler(ValueError)
async def value_error_handler(_: Request, exc: ValueError) -> JSONResponse:
    """ValueErrors propagate up from ``app.storage`` when a malicious id
    contains path separators or ``..``. Map to 400 invalid_input so the
    client sees the same envelope as other validation failures.
    """
    return JSONResponse(
        status_code=400,
        content={"error": "invalid_input", "message": str(exc)},
    )


@app.exception_handler(RequestValidationError)
async def request_validation_handler(
    _: Request, exc: RequestValidationError
) -> JSONResponse:
    """Pydantic schema failures default to FastAPI's own 422 envelope with
    a `detail` array. Override so every non-2xx response across the API
    keeps the contract envelope (`error` + `message`).
    """
    return JSONResponse(
        status_code=422,
        content={
            "error": "invalid_input",
            "message": "request body failed validation",
        },
    )


# Helpers ---------------------------------------------------------------------


async def _check_binary(path: str) -> bool:
    """Return True when `<path> -version` exits 0 — proves the binary can
    actually run (not just that the file exists on disk).

    Catches shared-library linkage failures like `libavdevice.so.61: cannot
    open shared object file` at startup, before we accept any upload.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            path, "-version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return False
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.error(
            "binary.check_failed",
            extra={"binary": path, "stderr": stderr.decode(errors='ignore')[:300]},
        )
        return False
    return True


async def _stream_to_disk(upload: UploadFile, dest: Path, limit: int) -> int:
    """Stream the upload body to disk, enforcing `limit` and avoiding OOM (F-5.3)."""
    total = 0
    with dest.open("wb") as out:
        while True:
            chunk = await upload.read(UPLOAD_CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise UploadTooLarge(limit)
            out.write(chunk)
    return total


async def _run_normalize_job(
    *,
    request: Request,
    file: UploadFile,
    job_id: str,
    work_dir: Path,
    progress_cb=None,
) -> tuple[Path, dict[str, str]]:
    settings: Settings = request.app.state.settings
    limiter: JobLimiter = request.app.state.limiter
    input_path = work_dir / f"{job_id}.in"
    input_bytes = await _stream_to_disk(file, input_path, settings.max_upload_bytes)
    return await _run_normalize_from_path(
        request=request,
        job_id=job_id,
        work_dir=work_dir,
        input_path=input_path,
        input_bytes=input_bytes,
        progress_cb=progress_cb,
    )


async def _run_normalize_from_path(
    *,
    request: Request,
    job_id: str,
    work_dir: Path,
    input_path: Path,
    input_bytes: int,
    progress_cb=None,
) -> tuple[Path, dict[str, str]]:
    settings: Settings = request.app.state.settings
    limiter: JobLimiter = request.app.state.limiter
    log_extra: dict[str, object] = {"job_id": job_id}

    log_extra["input_bytes"] = input_bytes
    metrics.INPUT_BYTES.observe(input_bytes)

    async with limiter.slot():
        outcome = await normalize_file(
            input_path=input_path,
            work_dir=work_dir,
            settings=settings,
            job_id=job_id,
            progress_cb=progress_cb,
        )

    output_size = outcome.output_path.stat().st_size
    metrics.OUTPUT_BYTES.observe(output_size)
    metrics.JOB_DURATION_SECONDS.labels(
        mode="remux" if outcome.remuxed else "encode"
    ).observe(outcome.duration_ms / 1000.0)
    metrics.JOBS_TOTAL.labels(outcome="success").inc()

    log_extra.update(
        {
            "output_bytes": output_size,
            "duration_ms": outcome.duration_ms,
            "input_codec": outcome.probe.video_codec,
            "input_format": outcome.probe.format_name,
            "remuxed": outcome.remuxed,
            "success": True,
        }
    )
    logger.info("normalize.done", extra=log_extra)
    headers = {
        "Content-Length": str(output_size),
        "X-Normalize-Duration-Ms": str(outcome.duration_ms),
        "X-Normalize-Input-Codec": outcome.probe.video_codec or "unknown",
        "X-Normalize-Remuxed": "1" if outcome.remuxed else "0",
        "Cache-Control": "no-store",
    }
    return outcome.output_path, headers


def _job_urls(request: Request, job_id: str) -> tuple[str, str]:
    return str(request.url_for("normalize_job_status", job_id=job_id)), str(
        request.url_for("normalize_job_result", job_id=job_id)
    )


async def _start_async_job(
    *,
    request: Request,
    job_id: str,
    work_dir: Path,
    input_path: Path,
    input_bytes: int,
) -> NormalizeJob:
    job = NormalizeJob(id=job_id, status="queued", work_dir=work_dir)
    async with request.app.state.jobs_lock:
        request.app.state.jobs[job_id] = job

    async def run() -> None:
        started = time.monotonic()
        try:
            job.status = "processing"

            async def on_progress(frac: float) -> None:
                job.progress = max(job.progress, frac)

            output_path, headers = await _run_normalize_from_path(
                request=request,
                job_id=job_id,
                work_dir=work_dir,
                input_path=input_path,
                input_bytes=input_bytes,
                progress_cb=on_progress,
            )
            job.progress = 1.0
            job.status = "done"
            job.output_path = output_path
            job.headers = headers
        except NormalizeError as exc:
            metrics.JOBS_TOTAL.labels(outcome=exc.code).inc()
            job.status = "failed"
            job.message = exc.message
            logger.warning(
                "normalize.fail",
                extra={
                    "job_id": job_id,
                    "error": exc.code,
                    "error_message": exc.message,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "success": False,
                },
            )
        except Exception:
            metrics.JOBS_TOTAL.labels(outcome="internal_error").inc()
            job.status = "failed"
            job.message = "internal error"
            logger.exception("normalize.crash", extra={"job_id": job_id})
    asyncio.create_task(run())
    return job


async def _enqueue_async_upload(
    *,
    request: Request,
    file: UploadFile,
    job_id: str,
    work_dir: Path,
) -> None:
    settings: Settings = request.app.state.settings
    input_path = work_dir / f"{job_id}.in"
    try:
        input_bytes = await _stream_to_disk(file, input_path, settings.max_upload_bytes)
    finally:
        await file.close()
    await _start_async_job(
        request=request,
        job_id=job_id,
        work_dir=work_dir,
        input_path=input_path,
        input_bytes=input_bytes,
    )


# Routes ----------------------------------------------------------------------


@app.get("/healthz")
async def healthz(request: Request) -> Response:
    ffmpeg_ok = getattr(request.app.state, "ffmpeg_ok", False)
    ffprobe_ok = getattr(request.app.state, "ffprobe_ok", False)
    if not ffmpeg_ok:
        return JSONResponse(
            status_code=503,
            content={"error": "ffmpeg_unavailable", "message": "ffmpeg binary cannot execute"},
        )
    if not ffprobe_ok:
        return JSONResponse(
            status_code=503,
            content={"error": "ffprobe_unavailable", "message": "ffprobe binary cannot execute"},
        )
    return JSONResponse({"status": "ok"})


@app.get("/metrics")
async def metrics_endpoint() -> Response:
    data = generate_latest(metrics.REGISTRY)
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)


@app.post("/v1/normalize")
async def normalize(
    request: Request,
    file: UploadFile,
    profile: str | None = None,
    async_job: bool = False,
    content_length: int | None = Header(default=None, alias="Content-Length"),
) -> Response:
    settings: Settings = request.app.state.settings

    # Short-circuit on Content-Length when present (F-1.2).
    if content_length is not None and content_length > settings.max_upload_bytes:
        raise UploadTooLarge(settings.max_upload_bytes)

    if profile and profile not in {"web-h264"}:
        # Unknown profiles are accepted but logged; reserved for future use.
        logger.info("unknown_profile", extra={"profile": profile})

    job_id = uuid.uuid4().hex
    work_dir = Path(tempfile.mkdtemp(prefix="normalize-", dir=settings.temp_dir))
    started = time.monotonic()

    try:
        if async_job:
            await _enqueue_async_upload(
                request=request,
                file=file,
                job_id=job_id,
                work_dir=work_dir,
            )
            status_url, result_url = _job_urls(request, job_id)
            return JSONResponse(
                status_code=202,
                content={
                    "jobId": job_id,
                    "statusUrl": status_url,
                    "resultUrl": result_url,
                },
            )

        output_path, headers = await _run_normalize_job(
            request=request,
            file=file,
            job_id=job_id,
            work_dir=work_dir,
        )

        async def streamer() -> AsyncIterator[bytes]:
            try:
                async for chunk in iter_file(output_path):
                    yield chunk
            finally:
                # F-5.6 / F-4.3: cleanup temp dir once the body is fully sent.
                shutil.rmtree(work_dir, ignore_errors=True)

        return StreamingResponse(
            streamer(),
            media_type="video/mp4",
            headers=headers,
        )

    except NormalizeError as exc:
        shutil.rmtree(work_dir, ignore_errors=True)
        metrics.JOBS_TOTAL.labels(outcome=exc.code).inc()
        logger.warning(
            "normalize.fail",
            extra={
                "job_id": job_id,
                "error": exc.code,
                "error_message": exc.message,
                "duration_ms": int((time.monotonic() - started) * 1000),
                "success": False,
            },
        )
        raise
    except Exception:
        shutil.rmtree(work_dir, ignore_errors=True)
        metrics.JOBS_TOTAL.labels(outcome="internal_error").inc()
        logger.exception("normalize.crash", extra={
            "job_id": job_id,
            "duration_ms": int((time.monotonic() - started) * 1000),
        })
        raise
    finally:
        if not async_job:
            await file.close()


@app.post("/v1/normalize/jobs")
async def normalize_async(
    request: Request,
    file: UploadFile,
    profile: str | None = None,
    content_length: int | None = Header(default=None, alias="Content-Length"),
) -> JSONResponse:
    settings: Settings = request.app.state.settings
    if content_length is not None and content_length > settings.max_upload_bytes:
        raise UploadTooLarge(settings.max_upload_bytes)
    if profile and profile not in {"web-h264"}:
        logger.info("unknown_profile", extra={"profile": profile})

    job_id = uuid.uuid4().hex
    work_dir = Path(tempfile.mkdtemp(prefix="normalize-", dir=settings.temp_dir))
    await _enqueue_async_upload(
        request=request,
        file=file,
        job_id=job_id,
        work_dir=work_dir,
    )
    status_url, result_url = _job_urls(request, job_id)
    return JSONResponse(
        status_code=202,
        content={"jobId": job_id, "statusUrl": status_url, "resultUrl": result_url},
    )


@app.get("/v1/normalize/jobs/{job_id}")
async def normalize_job_status(request: Request, job_id: str) -> JSONResponse:
    job = request.app.state.jobs.get(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": "not_found", "message": "job not found"})
    return JSONResponse(
        {
            "jobId": job.id,
            "status": job.status,
            "state": job.status,
            "progress": round(job.progress * 100, 2),
            "message": job.message,
        }
    )


@app.get("/v1/normalize/jobs/{job_id}/result")
async def normalize_job_result(request: Request, job_id: str) -> Response:
    job = request.app.state.jobs.get(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": "not_found", "message": "job not found"})
    if job.status == "failed":
        return JSONResponse(status_code=422, content={"error": "decode_failed", "message": job.message or "failed"})
    if job.status != "done" or job.output_path is None:
        return JSONResponse(status_code=409, content={"error": "not_ready", "message": "job is still running"})

    async def streamer() -> AsyncIterator[bytes]:
        try:
            async for chunk in iter_file(job.output_path):
                yield chunk
        finally:
            if job.work_dir is not None:
                shutil.rmtree(job.work_dir, ignore_errors=True)
            request.app.state.jobs.pop(job_id, None)

    return StreamingResponse(streamer(), media_type="video/mp4", headers=job.headers)


# --- Segmentation route -----------------------------------------------------


async def _run_segment_in_slot(
    *,
    request: Request,
    limiter: JobLimiter,
    settings: Settings,
    run,
    model: str,
):
    """Acquire a segment worker slot, then run `run()` in a thread.

    Two stability guarantees that the naive
    ``async with limiter.slot(): await asyncio.wait_for(asyncio.to_thread(run), …)``
    pattern lacks:

    1. **Bounded acquire wait.** We won't wait forever for a slot. If
       `segment_acquire_timeout_ms` elapses before we get one, we raise
       :class:`SegmentBusy` (HTTP 503) so the proxy's own timeout doesn't
       fire first and leave the client with an opaque hang.
    2. **Slot-tied to the worker thread, not the awaiter.** When inference
       exceeds `segment_timeout_ms` we surface 504 to the client *but the
       semaphore stays held* until the still-running OS thread exits.
       Python threads can't be cancelled cooperatively, so releasing the
       semaphore on `wait_for` timeout would let new requests pile new
       inferences on top of the stale one — the box ends up running far
       more concurrent inferences than `segment_max_concurrent`, and
       eventually wedges (no responses at all). Holding the slot caps
       real concurrency at the configured ceiling even under burst load.
    """
    try:
        await limiter.acquire(timeout=settings.segment_acquire_timeout_s)
    except asyncio.TimeoutError as exc:
        raise SegmentBusy("segmentation workers busy, retry shortly") from exc

    metrics.SEGMENT_CONCURRENT.set(limiter.active)
    metrics.SEGMENT_QUEUE_LENGTH.set(limiter.waiting)

    worker = asyncio.create_task(asyncio.to_thread(run))
    released = False

    async def _release_when_thread_exits() -> None:
        nonlocal released
        if released:
            return
        released = True
        try:
            await worker
        except Exception:
            pass
        finally:
            await limiter.release()
            metrics.SEGMENT_CONCURRENT.set(limiter.active)

    try:
        # `shield` prevents wait_for from cancelling the worker task on
        # timeout — the OS thread under it can't be cancelled anyway, so
        # cancelling its wrapper would just orphan the thread.
        result = await asyncio.wait_for(
            asyncio.shield(worker), timeout=settings.segment_timeout_s
        )
    except asyncio.TimeoutError:
        # Detach: respond 504 now, but keep the slot held until the
        # background inference truly finishes (or crashes).
        asyncio.create_task(_release_when_thread_exits())
        raise
    except BaseException:
        # Includes asyncio.CancelledError from a client disconnect. Let
        # the inference finish and free its slot in the background; never
        # release the semaphore from a path that doesn't actually own
        # the thread.
        asyncio.create_task(_release_when_thread_exits())
        raise

    # Worker already completed. Free the slot synchronously through the
    # same helper so book-keeping stays in one place.
    await _release_when_thread_exits()
    return result


async def _read_upload(file: UploadFile, limit: int) -> bytes:
    """Buffer a single small image into memory, enforcing `limit`."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(UPLOAD_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise UploadTooLarge(limit)
        chunks.append(chunk)
    return b"".join(chunks)


@app.post("/v1/segment")
async def segment(
    request: Request,
    file: UploadFile,
    region: str = Form(...),
    model: str = Form(SEGMENT_DEFAULT_MODEL),
    classHint: str | None = Form(default=None),
    content_length: int | None = Header(default=None, alias="Content-Length"),
) -> JSONResponse:
    """Re-fit an annotation to an object boundary.

    Single-image, single-region. Always returns a polygon when successful;
    the `X-Segment-Backend` header reports which CPU backend actually ran.
    """
    settings: Settings = request.app.state.settings
    if content_length is not None and content_length > settings.segment_max_upload_bytes:
        raise UploadTooLarge(settings.segment_max_upload_bytes)

    started = time.monotonic()
    file_bytes: bytes = b""
    try:
        file_bytes = await _read_upload(file, settings.segment_max_upload_bytes)
    finally:
        await file.close()

    limiter: JobLimiter = request.app.state.segment_limiter
    metrics.SEGMENT_QUEUE_LENGTH.set(limiter.waiting)

    # Shed load when the queue is already saturated. Without this guard a
    # burst of clients piles into an unbounded asyncio.Semaphore wait list
    # and the proxy's own timeout fires before any of them get a slot —
    # the user-visible symptom is "the server stopped responding". The
    # client retries 503 with backoff (see vision/segment.ts), so refusing
    # fast is friendlier than letting the request rot.
    max_queue = settings.segment_max_queue
    if max_queue > 0 and limiter.waiting >= max_queue:
        metrics.SEGMENT_TOTAL.labels(outcome="busy", model=model).inc()
        logger.warning(
            "segment.busy",
            extra={"model": model, "waiting": limiter.waiting, "active": limiter.active},
        )
        return JSONResponse(
            status_code=503,
            content={
                "error": "busy",
                "message": "segmentation queue full, retry shortly",
            },
            headers={"Retry-After": "1"},
        )

    def _run() -> object:
        return segment_image(
            file_bytes=file_bytes,
            region_raw=region,
            model=model,
            class_hint=classHint,
            crop_padding=settings.segment_crop_padding,
            epsilon_norm=settings.segment_polygon_epsilon,
        )

    try:
        result = await _run_segment_in_slot(
            request=request,
            limiter=limiter,
            settings=settings,
            run=_run,
            model=model,
        )
    except asyncio.TimeoutError:
        metrics.SEGMENT_TOTAL.labels(outcome="timeout", model=model).inc()
        logger.warning("segment.timeout", extra={"model": model})
        return JSONResponse(
            status_code=504,
            content={"error": "timeout", "message": "segmentation exceeded budget"},
        )
    except SegmentBusy as exc:
        # Acquire-timeout: never got a worker slot. Treat as 503 + Retry-After
        # so the client backs off instead of hammering an overloaded box.
        metrics.SEGMENT_TOTAL.labels(outcome="busy", model=model).inc()
        logger.warning(
            "segment.acquire_timeout",
            extra={"model": model, "waiting": limiter.waiting, "active": limiter.active},
        )
        return JSONResponse(
            status_code=exc.status,
            content={"error": exc.code, "message": exc.message},
            headers={"Retry-After": "2"},
        )
    except SegmentError as exc:
        metrics.SEGMENT_TOTAL.labels(outcome=exc.code, model=model).inc()
        # Re-raise so the central exception handler shapes the response.
        raise
    except Exception as exc:  # noqa: BLE001 - convert to 500
        metrics.SEGMENT_TOTAL.labels(outcome="internal_error", model=model).inc()
        logger.exception("segment.crash", extra={"model": model})
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "message": "segmentation failed"},
        )

    duration_ms = int((time.monotonic() - started) * 1000)
    if result is None:
        metrics.SEGMENT_TOTAL.labels(outcome="no_object", model=model).inc()
        # No-op contract: empty body, 200. Client preserves the existing label.
        return JSONResponse(
            status_code=200,
            content={},
            headers={"X-Segment-Duration-Ms": str(duration_ms)},
        )

    metrics.SEGMENT_TOTAL.labels(outcome="success", model=model).inc()
    metrics.SEGMENT_DURATION_SECONDS.labels(
        model=model, backend=result.backend_id
    ).observe(duration_ms / 1000.0)
    logger.info(
        "segment.done",
        extra={
            "model": model,
            "backend": result.backend_id,
            "duration_ms": duration_ms,
            "score": result.score,
            "ring_count": len(result.polygon),
            "point_count": sum(len(r) for r in result.polygon),
        },
    )
    return JSONResponse(
        status_code=200,
        content=result.to_response(),
        headers={
            "X-Segment-Backend": result.backend_id,
            "X-Segment-Duration-Ms": str(duration_ms),
        },
    )


@app.get("/v1/segment/models")
async def segment_models() -> JSONResponse:
    """Introspection: which model ids the server accepts and the backend each maps to."""
    from .segment.registry import _ROUTING  # local import to avoid leaking internals

    items = []
    for model_id in SEGMENT_SUPPORTED_MODELS:
        spec = _ROUTING.get(model_id)
        items.append(
            {
                "id": model_id,
                "default": model_id == SEGMENT_DEFAULT_MODEL,
                "backend": spec.backend_id if spec else None,
            }
        )
    return JSONResponse({"models": items, "default": SEGMENT_DEFAULT_MODEL})


def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.port,
        log_config=None,
    )


if __name__ == "__main__":
    main()
