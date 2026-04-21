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
from typing import AsyncIterator

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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    app.state.settings = settings
    app.state.limiter = JobLimiter(settings.max_concurrent_jobs)
    app.state.ffmpeg_ok = await _check_ffmpeg(settings)
    logger.info(
        "startup",
        extra={
            "max_concurrent_jobs": settings.max_concurrent_jobs,
            "max_upload_bytes": settings.max_upload_bytes,
            "job_timeout_ms": settings.job_timeout_ms,
            "ffmpeg_ok": app.state.ffmpeg_ok,
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


async def _check_ffmpeg(settings: Settings) -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            settings.ffmpeg_path, "-version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        return proc.returncode == 0
    except FileNotFoundError:
        return False


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


# Routes ----------------------------------------------------------------------


@app.get("/healthz")
async def healthz(request: Request) -> Response:
    ok = getattr(request.app.state, "ffmpeg_ok", False)
    if not ok:
        return JSONResponse(
            status_code=503,
            content={"error": "ffmpeg_unavailable", "message": "ffmpeg binary not found"},
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
    content_length: int | None = Header(default=None, alias="Content-Length"),
) -> Response:
    settings: Settings = request.app.state.settings
    limiter: JobLimiter = request.app.state.limiter

    # Short-circuit on Content-Length when present (F-1.2).
    if content_length is not None and content_length > settings.max_upload_bytes:
        raise UploadTooLarge(settings.max_upload_bytes)

    if profile and profile not in {"web-h264"}:
        # Unknown profiles are accepted but logged; reserved for future use.
        logger.info("unknown_profile", extra={"profile": profile})

    job_id = uuid.uuid4().hex
    work_dir = Path(tempfile.mkdtemp(prefix="normalize-", dir=settings.temp_dir))
    input_path = work_dir / f"{job_id}.in"
    started = time.monotonic()
    log_extra: dict[str, object] = {"job_id": job_id}

    try:
        input_bytes = await _stream_to_disk(file, input_path, settings.max_upload_bytes)
        log_extra["input_bytes"] = input_bytes
        metrics.INPUT_BYTES.observe(input_bytes)

        async with limiter.slot():
            outcome = await normalize_file(
                input_path=input_path,
                work_dir=work_dir,
                settings=settings,
                job_id=job_id,
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

        output_path = outcome.output_path

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
        log_extra.update(
            {
                "success": False,
                "error": exc.code,
                "error_message": exc.message,
                "duration_ms": int((time.monotonic() - started) * 1000),
            }
        )
        logger.warning("normalize.fail", extra=log_extra)
        raise
    except Exception:
        shutil.rmtree(work_dir, ignore_errors=True)
        metrics.JOBS_TOTAL.labels(outcome="internal_error").inc()
        log_extra["duration_ms"] = int((time.monotonic() - started) * 1000)
        logger.exception("normalize.crash", extra=log_extra)
        raise
    finally:
        await file.close()


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
