from __future__ import annotations


class NormalizeError(Exception):
    """Structured error carrying an error code, a human message, and an HTTP status."""

    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class UploadTooLarge(NormalizeError):
    def __init__(self, limit: int) -> None:
        super().__init__(
            "upload_too_large",
            f"upload exceeds {limit} bytes",
            413,
        )


class InvalidInput(NormalizeError):
    def __init__(self, message: str) -> None:
        super().__init__("invalid_input", message, 422)


class UnsupportedMediaType(NormalizeError):
    def __init__(self, message: str) -> None:
        super().__init__("unsupported_media_type", message, 415)


class NoVideoStream(NormalizeError):
    def __init__(self) -> None:
        super().__init__("no_video_stream", "input has no video stream", 422)


class JobTimeout(NormalizeError):
    def __init__(self, seconds: float) -> None:
        super().__init__(
            "timeout",
            f"job exceeded {seconds:.0f}s",
            504,
        )


class FfmpegFailed(NormalizeError):
    def __init__(self, stderr: str) -> None:
        super().__init__(
            "ffmpeg_failed",
            f"ffmpeg failed: {stderr[:500]}",
            422,
        )


class FfprobeUnavailable(NormalizeError):
    """The ffprobe binary itself cannot execute (missing libs, not found, etc.).

    Not the user's fault — map to 503 so clients fall back to the wasm path.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(
            "ffprobe_unavailable",
            f"ffprobe cannot execute: {detail[:300]}",
            503,
        )
