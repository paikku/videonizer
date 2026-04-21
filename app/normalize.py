from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator

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


async def _run_ffmpeg(cmd: list[str], timeout_s: float) -> tuple[int, str]:
    logger.info("ffmpeg.spawn", extra={"argv": cmd})
    # start_new_session=True so we can signal the whole process group on timeout.
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
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
    stderr_text = (stderr or b"").decode(errors="ignore")
    return proc.returncode or 0, stderr_text


async def normalize_file(
    input_path: Path,
    work_dir: Path,
    settings: Settings,
    job_id: str | None = None,
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
        extra_args=settings.ffmpeg_extra_args_list,
    )

    rc, stderr = await _run_ffmpeg(cmd, settings.job_timeout_s)
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
