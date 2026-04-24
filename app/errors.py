from __future__ import annotations


class ServiceError(Exception):
    """Base for all structured API errors. Carries error code, human message, HTTP status."""

    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class NormalizeError(ServiceError):
    """Errors raised by the /v1/normalize pipeline."""


class SegmentError(ServiceError):
    """Errors raised by the /v1/segment pipeline."""


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


# --- Segment errors ---------------------------------------------------------


class SegmentUnsupportedModel(SegmentError):
    def __init__(self, model: str) -> None:
        # Message text matches the contract in the requirements doc.
        super().__init__("unsupported model", f"unsupported model: {model}", 400)


class SegmentInvalidRegion(SegmentError):
    def __init__(self, message: str) -> None:
        super().__init__("invalid_region", message, 400)


class SegmentImageDecodeFailed(SegmentError):
    def __init__(self, message: str) -> None:
        super().__init__("image_decode_failed", message, 400)


class SegmentUnsupportedImage(SegmentError):
    def __init__(self, message: str) -> None:
        super().__init__("unsupported_media_type", message, 415)


class SegmentBackendUnavailable(SegmentError):
    """A model backend can't load (missing weights, missing dep, OOM)."""

    def __init__(self, message: str) -> None:
        super().__init__("backend_unavailable", message, 503)


class SegmentBusy(SegmentError):
    def __init__(self, message: str = "all segment workers busy") -> None:
        super().__init__("busy", message, 503)
