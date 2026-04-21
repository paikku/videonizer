from __future__ import annotations

from pathlib import Path

from app.normalize import _progress_fraction_from_line, build_ffmpeg_cmd
from app.probe import ProbeResult


def _probe(**overrides) -> ProbeResult:
    base = dict(
        format_name="mov,mp4,m4a,3gp,3g2,mj2",
        duration=10.0,
        has_video=True,
        has_audio=True,
        video_codec="h264",
        audio_codec="aac",
        width=1920,
        height=1080,
        rotation=0,
    )
    base.update(overrides)
    return ProbeResult(**base)


def test_remux_when_already_web_compatible() -> None:
    probe = _probe()
    assert probe.is_web_compatible

    cmd = build_ffmpeg_cmd(
        ffmpeg_path="ffmpeg",
        input_path=Path("/tmp/in.mp4"),
        output_path=Path("/tmp/out.mp4"),
        probe=probe,
        extra_args=[],
    )
    assert "-c" in cmd and "copy" in cmd
    assert "-movflags" in cmd
    assert "+faststart" in cmd
    assert "libx264" not in cmd


def test_reencode_when_codec_differs() -> None:
    probe = _probe(video_codec="mpeg4", format_name="avi")
    cmd = build_ffmpeg_cmd(
        ffmpeg_path="ffmpeg",
        input_path=Path("/tmp/in.avi"),
        output_path=Path("/tmp/out.mp4"),
        probe=probe,
        extra_args=[],
    )
    # F-2.1
    assert cmd[cmd.index("-c:v") + 1] == "libx264"
    assert cmd[cmd.index("-profile:v") + 1] == "main"
    assert cmd[cmd.index("-pix_fmt") + 1] == "yuv420p"
    # F-2.2
    assert cmd[cmd.index("-c:a") + 1] == "aac"
    assert cmd[cmd.index("-b:a") + 1] == "128k"
    # F-2.3
    assert cmd[cmd.index("-movflags") + 1] == "+faststart"
    # F-2.4
    assert "scale=trunc(iw/2)*2:trunc(ih/2)*2" in cmd
    # F-3.1
    assert cmd[cmd.index("-fps_mode") + 1] == "passthrough"


def test_audio_flags_omitted_when_no_audio() -> None:
    probe = _probe(has_audio=False, audio_codec=None, video_codec="wmv3", format_name="asf")
    cmd = build_ffmpeg_cmd(
        ffmpeg_path="ffmpeg",
        input_path=Path("/tmp/in.wmv"),
        output_path=Path("/tmp/out.mp4"),
        probe=probe,
        extra_args=[],
    )
    assert "-c:a" not in cmd
    assert "-b:a" not in cmd


def test_rotation_forces_reencode_and_strips_metadata() -> None:
    probe = _probe(rotation=90)
    assert not probe.is_web_compatible
    cmd = build_ffmpeg_cmd(
        ffmpeg_path="ffmpeg",
        input_path=Path("/tmp/in.mp4"),
        output_path=Path("/tmp/out.mp4"),
        probe=probe,
        extra_args=[],
    )
    idx = cmd.index("-metadata:s:v:0")
    assert cmd[idx + 1] == "rotate=0"


def test_odd_dimensions_force_reencode() -> None:
    probe = _probe(width=1921, height=1081)
    assert not probe.is_web_compatible


def test_extra_args_injected_before_output() -> None:
    probe = _probe(video_codec="flv1", format_name="flv")
    cmd = build_ffmpeg_cmd(
        ffmpeg_path="ffmpeg",
        input_path=Path("/tmp/in.flv"),
        output_path=Path("/tmp/out.mp4"),
        probe=probe,
        extra_args=["-preset", "veryfast"],
    )
    assert cmd[-1] == "/tmp/out.mp4"
    assert cmd[-3:-1] == ["-preset", "veryfast"]


def test_no_shell_interpolation() -> None:
    """F-5.2: paths go through argv, never shell-quoted strings."""
    probe = _probe(video_codec="mpeg4", format_name="avi")
    evil = Path("/tmp/$(rm -rf /).avi")
    cmd = build_ffmpeg_cmd(
        ffmpeg_path="ffmpeg",
        input_path=evil,
        output_path=Path("/tmp/out.mp4"),
        probe=probe,
        extra_args=[],
    )
    # The literal path must appear as a single argv element, unescaped.
    assert str(evil) in cmd


def test_progress_fraction_from_line_parses_out_time_ms() -> None:
    frac = _progress_fraction_from_line("out_time_ms=5000000\n", duration_s=10.0)
    assert frac == 0.5


def test_progress_fraction_from_line_ignores_unrelated_lines() -> None:
    assert _progress_fraction_from_line("progress=continue\n", duration_s=10.0) is None
