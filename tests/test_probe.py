from app.probe import ProbeResult, _extract_rotation


def test_web_compatible_happy_path() -> None:
    p = ProbeResult(
        format_name="mov,mp4,m4a,3gp,3g2,mj2",
        duration=5.0,
        has_video=True,
        has_audio=True,
        video_codec="h264",
        audio_codec="aac",
        width=1280,
        height=720,
        rotation=0,
    )
    assert p.is_web_compatible is True


def test_not_web_compatible_when_audio_wrong() -> None:
    p = ProbeResult(
        format_name="mov,mp4,m4a,3gp,3g2,mj2",
        duration=5.0,
        has_video=True,
        has_audio=True,
        video_codec="h264",
        audio_codec="mp3",
        width=1280,
        height=720,
        rotation=0,
    )
    assert p.is_web_compatible is False


def test_rotation_from_tags() -> None:
    stream = {"tags": {"rotate": "90"}}
    assert _extract_rotation(stream) == 90


def test_rotation_from_display_matrix() -> None:
    stream = {
        "side_data_list": [
            {"side_data_type": "Display Matrix", "rotation": -90},
        ]
    }
    assert _extract_rotation(stream) == -90


def test_rotation_absent() -> None:
    assert _extract_rotation({}) == 0
