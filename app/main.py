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

from fastapi import FastAPI, Header, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from . import metrics
from .config import Settings, get_settings
from .errors import NormalizeError, UploadTooLarge
from .jobs import JobLimiter
from .logging_conf import configure_logging
from .normalize import iter_file, normalize_file

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
    app.state.jobs: dict[str, NormalizeJob] = {}
    app.state.jobs_lock = asyncio.Lock()
    app.state.ffmpeg_ok = await _check_binary(settings.ffmpeg_path)
    app.state.ffprobe_ok = await _check_binary(settings.ffprobe_path)
    logger.info(
        "startup",
        extra={
            "max_concurrent_jobs": settings.max_concurrent_jobs,
            "max_upload_bytes": settings.max_upload_bytes,
            "job_timeout_ms": settings.job_timeout_ms,
            "ffmpeg_ok": app.state.ffmpeg_ok,
            "ffprobe_ok": app.state.ffprobe_ok,
        },
    )
    yield


app = FastAPI(title="Videonizer Normalize Service", version="0.1.0", lifespan=lifespan)


# CORS ------------------------------------------------------------------------

_settings_boot = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings_boot.allowed_origins_list or [],
    allow_credentials=False,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=[
        "X-Normalize-Duration-Ms",
        "X-Normalize-Input-Codec",
        "X-Normalize-Remuxed",
        "Content-Length",
    ],
)


# Error handling --------------------------------------------------------------


@app.exception_handler(NormalizeError)
async def normalize_error_handler(_: Request, exc: NormalizeError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content={"error": exc.code, "message": exc.message},
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
    log_extra: dict[str, object] = {"job_id": job_id}

    input_bytes = await _stream_to_disk(file, input_path, settings.max_upload_bytes)
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
    file: UploadFile,
    job_id: str,
    work_dir: Path,
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

            output_path, headers = await _run_normalize_job(
                request=request,
                file=file,
                job_id=job_id,
                work_dir=work_dir,
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
        finally:
            await file.close()

    asyncio.create_task(run())
    return job


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
            await _start_async_job(
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
    await _start_async_job(
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
