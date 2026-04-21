from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from .errors import InvalidInput, NoVideoStream


@dataclass
class ProbeResult:
    format_name: str
    duration: float
    has_video: bool
    has_audio: bool
    video_codec: str | None
    audio_codec: str | None
    width: int
    height: int
    rotation: int

    @property
    def is_web_compatible(self) -> bool:
        """True when a remux (no re-encode) would satisfy F-2.*."""
        if self.video_codec != "h264":
            return False
        if self.has_audio and self.audio_codec != "aac":
            return False
        if "mp4" not in self.format_name:
            return False
        if self.rotation % 360 != 0:
            return False
        if self.width % 2 != 0 or self.height % 2 != 0:
            return False
        return True


def _extract_rotation(video_stream: dict) -> int:
    tags = video_stream.get("tags") or {}
    if "rotate" in tags:
        try:
            return int(tags["rotate"])
        except (TypeError, ValueError):
            pass
    for sd in video_stream.get("side_data_list") or []:
        if sd.get("side_data_type") == "Display Matrix":
            try:
                return int(sd.get("rotation", 0))
            except (TypeError, ValueError):
                pass
    return 0


async def ffprobe(path: Path, ffprobe_path: str) -> ProbeResult:
    proc = await asyncio.create_subprocess_exec(
        ffprobe_path,
        "-v", "error",
        "-show_format",
        "-show_streams",
        "-of", "json",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise InvalidInput(
            f"ffprobe failed: {stderr.decode(errors='ignore').strip()[:300]}"
        )

    try:
        data = json.loads(stdout or b"{}")
    except json.JSONDecodeError as exc:
        raise InvalidInput(f"ffprobe returned non-JSON: {exc}") from exc

    streams = data.get("streams") or []
    format_data = data.get("format") or {}

    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if video is None:
        raise NoVideoStream()

    try:
        duration = float(format_data.get("duration", 0.0))
    except (TypeError, ValueError):
        duration = 0.0

    return ProbeResult(
        format_name=format_data.get("format_name", ""),
        duration=duration,
        has_video=True,
        has_audio=audio is not None,
        video_codec=video.get("codec_name"),
        audio_codec=audio.get("codec_name") if audio else None,
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        rotation=_extract_rotation(video),
    )
