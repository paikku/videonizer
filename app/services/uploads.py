"""Streaming upload helpers — common to resources, images, previews.

Pattern mirrors the existing ``app/main.py::_stream_to_disk`` (1 MiB chunks
with cumulative size guard) but lands the bytes in S3 (or wherever the
configured BlobStore goes) without ever staging the full file on disk.

The legacy normalize/segment paths still go through the disk-buffered helper
because ffmpeg needs a real file path; the stateful API does not.
"""
from __future__ import annotations

import io
import tempfile
from pathlib import Path
from typing import BinaryIO

from fastapi import UploadFile

from ..errors import UploadTooLarge
from ..storage.blobs import BlobMeta, BlobStore

UPLOAD_CHUNK = 1024 * 1024  # 1 MiB


async def buffer_upload(
    upload: UploadFile, *, limit: int, temp_dir: str | None = None
) -> tuple[Path, int]:
    """Stream an UploadFile to a tmp file with a hard size cap. Returns
    ``(path, total_bytes)``. Caller is responsible for unlinking the path.
    """
    fd, raw_path = tempfile.mkstemp(prefix="videonizer-upload-", dir=temp_dir)
    path = Path(raw_path)
    total = 0
    try:
        with open(fd, "wb") as out:
            while True:
                chunk = await upload.read(UPLOAD_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise UploadTooLarge(limit)
                out.write(chunk)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path, total


async def stream_upload_to_blob(
    upload: UploadFile,
    *,
    store: BlobStore,
    key: str,
    content_type: str,
    limit: int,
    temp_dir: str | None = None,
) -> BlobMeta:
    """Buffer the upload to a temp file (with cap), then PUT to the blob
    store via boto3 multipart upload. The temp file is removed afterward.

    boto3 needs a seekable file-like, so we can't pipe straight from
    UploadFile.read() into upload_fileobj. The temp file is the price.
    """
    path, _total = await buffer_upload(upload, limit=limit, temp_dir=temp_dir)
    try:
        with open(path, "rb") as fh:
            return await store.put_stream(key, fh, content_type)
    finally:
        path.unlink(missing_ok=True)


async def buffer_to_memory(upload: UploadFile, *, limit: int) -> bytes:
    """Buffer a small upload (e.g. one preview tile, one image) entirely in
    memory. Same size-guard semantics as ``buffer_upload`` but no disk hop.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(UPLOAD_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise UploadTooLarge(limit)
        chunks.append(chunk)
    return b"".join(chunks)
