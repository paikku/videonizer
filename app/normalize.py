from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable

from .config import Settings
from .errors import FfmpegFailed, JobTimeout
from .probe import ProbeResult, ffprobe

logger = logging.getLogger("videonizer.normalize")


@dataclass
class NormalizeOutcome:
    output_path: Path
    probe: ProbeResult
    duration_ms: int
    remuxed: bool


def build_ffmpeg_cmd(
    ffmpeg_path: str,
    input_path: Path,
    output_path: Path,
    probe: ProbeResult,
    extra_args: list[str],
) -> list[str]:
    """Build the ffmpeg argv matching the wasm adapter (F-2.*) and timing rules (F-3.*).

    - Remux-only path when input is already H.264/AAC/MP4 with no rotation and even dims.
    - Otherwise: libx264 main + yuv420p + AAC 128k + faststart with even-scale filter
      and passthrough frame timing for VFR preservation.
    """
    cmd: list[str] = [
        ffmpeg_path,
        "-nostdin",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-i", str(input_path),
    ]

    if probe.is_web_compatible:
        cmd += [
            "-map", "0:v:0",
        ]
        if probe.has_audio:
            cmd += ["-map", "0:a:0?"]
        cmd += [
            "-c", "copy",
            "-movflags", "+faststart",
        ]
    else:
        cmd += ["-map", "0:v:0"]
        if probe.has_audio:
            cmd += ["-map", "0:a:0?"]
        cmd += [
            "-c:v", "libx264",
            "-profile:v", "main",
            "-pix_fmt", "yuv420p",
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-fps_mode", "passthrough",
        ]
        if probe.has_audio:
            cmd += ["-c:a", "aac", "-b:a", "128k"]
        # Bake rotation into pixels and strip metadata (F-3.2).
        cmd += ["-metadata:s:v:0", "rotate=0"]
        cmd += ["-movflags", "+faststart"]

    cmd += extra_args
    cmd += [str(output_path)]
    return cmd


ProgressCallback = Callable[[float], Awaitable[None] | None]


def _progress_fraction_from_line(line: str, duration_s: float) -> float | None:
    if duration_s <= 0 or not line.startswith("out_time_ms="):
        return None
    raw = line.split("=", 1)[1].strip()
    if not raw.isdigit():
        return None
    out_time_ms = int(raw)
    fraction = out_time_ms / (duration_s * 1_000_000)
    return max(0.0, min(1.0, fraction))


async def _run_ffmpeg(
    cmd: list[str],
    timeout_s: float,
    *,
    progress_cb: ProgressCallback | None = None,
    duration_s: float = 0.0,
) -> tuple[int, str]:
    logger.info("ffmpeg.spawn", extra={"argv": cmd})
    # start_new_session=True so we can signal the whole process group on timeout.
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )

    async def wait_with_streamed_progress() -> tuple[int, str]:
        assert proc.stderr is not None
        stderr_parts: list[str] = []
        latest_progress = 0.0
        while True:
            line_bytes = await proc.stderr.readline()
            if not line_bytes:
                break
            line = line_bytes.decode(errors="ignore")
            stderr_parts.append(line)
            if progress_cb is None:
                continue
            frac = _progress_fraction_from_line(line, duration_s)
            if frac is None or frac <= latest_progress:
                continue
            latest_progress = frac
            maybe = progress_cb(frac)
            if asyncio.iscoroutine(maybe):
                await maybe
        rc = await proc.wait()
        return rc, "".join(stderr_parts)

    try:
        rc, stderr_text = await asyncio.wait_for(wait_with_streamed_progress(), timeout=timeout_s)
    except asyncio.TimeoutError:
        logger.warning("ffmpeg.timeout", extra={"pid": proc.pid})
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            pass
        raise JobTimeout(timeout_s)
    return rc or 0, stderr_text


async def normalize_file(
    input_path: Path,
    work_dir: Path,
    settings: Settings,
    job_id: str | None = None,
    progress_cb: ProgressCallback | None = None,
) -> NormalizeOutcome:
    """Run ffprobe + ffmpeg on `input_path`, writing an MP4 into `work_dir`."""
    job_id = job_id or uuid.uuid4().hex
    started = time.monotonic()

    probe = await ffprobe(input_path, settings.ffprobe_path)
    output_path = work_dir / f"{job_id}.mp4"

    cmd = build_ffmpeg_cmd(
        ffmpeg_path=settings.ffmpeg_path,
        input_path=input_path,
        output_path=output_path,
        probe=probe,
        extra_args=["-progress", "pipe:2", "-nostats", *settings.ffmpeg_extra_args_list],
    )

    rc, stderr = await _run_ffmpeg(
        cmd,
        settings.job_timeout_s,
        progress_cb=progress_cb,
        duration_s=probe.duration,
    )
    if rc != 0 or not output_path.exists() or output_path.stat().st_size == 0:
        raise FfmpegFailed(stderr)

    elapsed_ms = int((time.monotonic() - started) * 1000)
    return NormalizeOutcome(
        output_path=output_path,
        probe=probe,
        duration_ms=elapsed_ms,
        remuxed=probe.is_web_compatible,
    )


async def iter_file(path: Path, chunk_size: int = 1024 * 1024) -> AsyncIterator[bytes]:
    loop = asyncio.get_running_loop()
    f = await loop.run_in_executor(None, path.open, "rb")
    try:
        while True:
            chunk = await loop.run_in_executor(None, f.read, chunk_size)
            if not chunk:
                break
            yield chunk
    finally:
        await loop.run_in_executor(None, f.close)
