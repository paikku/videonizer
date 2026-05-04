"""HTTP Range header parsing + 206 response helpers.

Single-range only — the ``<video>`` element never sends multipart/byteranges
in practice and we don't want to maintain a multipart writer for a feature
nobody uses.
"""
from __future__ import annotations

import re
from typing import AsyncIterator

from fastapi.responses import Response, StreamingResponse


_RANGE_RE = re.compile(r"^\s*bytes=(\d*)-(\d*)\s*$")


class InvalidRange(Exception):
    """Raised when a Range header parses but is unsatisfiable for the size."""


def parse_range(header: str | None, total: int) -> tuple[int, int] | None:
    """Return ``(start, end_inclusive)`` or ``None`` if no header present.

    Raises :class:`InvalidRange` when the header is syntactically valid but
    the range is unsatisfiable (caller should respond 416).
    """
    if not header:
        return None
    m = _RANGE_RE.match(header)
    if not m:
        raise InvalidRange(f"malformed range header: {header!r}")
    raw_start, raw_end = m.group(1), m.group(2)

    if raw_start == "" and raw_end == "":
        raise InvalidRange("range must specify start, end, or both")

    if raw_start == "":
        # Suffix range: last N bytes.
        suffix = int(raw_end)
        if suffix <= 0:
            raise InvalidRange("suffix range must be positive")
        start = max(total - suffix, 0)
        end = total - 1
    elif raw_end == "":
        start = int(raw_start)
        end = total - 1
    else:
        start = int(raw_start)
        end = int(raw_end)

    if start >= total or end < start:
        raise InvalidRange(f"unsatisfiable range {start}-{end} for total {total}")
    if end >= total:
        end = total - 1
    return start, end


def range_not_satisfiable(total: int) -> Response:
    return Response(
        status_code=416,
        headers={"Content-Range": f"bytes */{total}"},
    )


def stream_full(
    body: AsyncIterator[bytes],
    *,
    total: int,
    media_type: str,
    extra_headers: dict[str, str] | None = None,
) -> StreamingResponse:
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(total),
    }
    if extra_headers:
        headers.update(extra_headers)
    return StreamingResponse(body, media_type=media_type, headers=headers)


def stream_range(
    body: AsyncIterator[bytes],
    *,
    start: int,
    end: int,
    total: int,
    media_type: str,
    extra_headers: dict[str, str] | None = None,
) -> StreamingResponse:
    length = end - start + 1
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Range": f"bytes {start}-{end}/{total}",
        "Content-Length": str(length),
    }
    if extra_headers:
        headers.update(extra_headers)
    return StreamingResponse(
        body, status_code=206, media_type=media_type, headers=headers
    )
