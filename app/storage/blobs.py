"""Object-storage abstraction used by the stateful API.

Single concrete impl: :class:`S3BlobStore` (boto3, S3 / MinIO compatible).
Tests mock the S3 endpoint via moto so this same class is exercised.

All keys are plain strings of the form ``p/<pid>/r/<rid>/source.<ext>`` —
the convention is documented in ``WORK_PLAN.md`` §2 and not enforced here.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import AsyncIterator, BinaryIO, Iterable, Protocol

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError


@dataclass
class BlobMeta:
    size: int
    content_type: str
    etag: str


class BlobStore(Protocol):
    async def put_bytes(self, key: str, data: bytes, content_type: str) -> BlobMeta: ...
    async def put_stream(
        self, key: str, fileobj: BinaryIO, content_type: str
    ) -> BlobMeta: ...
    async def get_bytes(self, key: str) -> tuple[bytes, BlobMeta]: ...
    async def get_range(
        self, key: str, start: int, end: int | None
    ) -> tuple[AsyncIterator[bytes], BlobMeta, int]: ...
    async def head(self, key: str) -> BlobMeta: ...
    async def exists(self, key: str) -> bool: ...
    async def delete(self, key: str) -> None: ...
    async def delete_prefix(self, prefix: str) -> int: ...


class BlobNotFound(Exception):
    pass


class S3BlobStore:
    """boto3-backed BlobStore. All sync boto calls are pushed onto threads
    so the FastAPI event loop never blocks on network IO."""

    def __init__(
        self,
        *,
        endpoint: str,
        region: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        force_path_style: bool = True,
    ) -> None:
        self.bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint or None,
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=BotoConfig(
                s3={"addressing_style": "path" if force_path_style else "auto"},
                signature_version="s3v4",
                # Don't retry forever on a single PUT — surface failures fast
                # so callers can decide.
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )

    # --- bucket lifecycle --------------------------------------------------

    async def ensure_bucket(self) -> None:
        def _do() -> None:
            try:
                self._client.head_bucket(Bucket=self.bucket)
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code in {"404", "NoSuchBucket", "NotFound"}:
                    self._client.create_bucket(Bucket=self.bucket)
                    return
                raise

        await asyncio.to_thread(_do)

    # --- writes ------------------------------------------------------------

    async def put_bytes(self, key: str, data: bytes, content_type: str) -> BlobMeta:
        def _do() -> BlobMeta:
            resp = self._client.put_object(
                Bucket=self.bucket, Key=key, Body=data, ContentType=content_type
            )
            return BlobMeta(
                size=len(data),
                content_type=content_type,
                etag=resp.get("ETag", "").strip('"'),
            )

        return await asyncio.to_thread(_do)

    async def put_stream(
        self, key: str, fileobj: BinaryIO, content_type: str
    ) -> BlobMeta:
        """Multipart upload from a sync file-like object. Caller must rewind
        if reusing the object after this call.
        """

        def _do() -> BlobMeta:
            self._client.upload_fileobj(
                fileobj,
                self.bucket,
                key,
                ExtraArgs={"ContentType": content_type},
            )
            head = self._client.head_object(Bucket=self.bucket, Key=key)
            return BlobMeta(
                size=int(head["ContentLength"]),
                content_type=head.get("ContentType", content_type),
                etag=head.get("ETag", "").strip('"'),
            )

        return await asyncio.to_thread(_do)

    # --- reads -------------------------------------------------------------

    async def head(self, key: str) -> BlobMeta:
        def _do() -> BlobMeta:
            try:
                head = self._client.head_object(Bucket=self.bucket, Key=key)
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code in {"404", "NoSuchKey", "NotFound"}:
                    raise BlobNotFound(key) from exc
                raise
            return BlobMeta(
                size=int(head["ContentLength"]),
                content_type=head.get("ContentType", "application/octet-stream"),
                etag=head.get("ETag", "").strip('"'),
            )

        return await asyncio.to_thread(_do)

    async def exists(self, key: str) -> bool:
        try:
            await self.head(key)
            return True
        except BlobNotFound:
            return False

    async def get_bytes(self, key: str) -> tuple[bytes, BlobMeta]:
        def _do() -> tuple[bytes, BlobMeta]:
            try:
                resp = self._client.get_object(Bucket=self.bucket, Key=key)
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code in {"404", "NoSuchKey"}:
                    raise BlobNotFound(key) from exc
                raise
            body = resp["Body"].read()
            meta = BlobMeta(
                size=int(resp.get("ContentLength", len(body))),
                content_type=resp.get("ContentType", "application/octet-stream"),
                etag=resp.get("ETag", "").strip('"'),
            )
            return body, meta

        return await asyncio.to_thread(_do)

    async def get_range(
        self, key: str, start: int, end: int | None
    ) -> tuple[AsyncIterator[bytes], BlobMeta, int]:
        """Range read. ``end`` is inclusive (HTTP semantics), or ``None`` for
        ``start..EOF``. Returns (chunk iterator, blob meta, total size).
        The iterator yields chunks pulled from a background thread so the
        event loop stays free.
        """
        meta = await self.head(key)
        total = meta.size
        if start < 0 or start >= total:
            raise ValueError(f"range start {start} out of bounds for size {total}")
        actual_end = total - 1 if end is None or end >= total else end
        if actual_end < start:
            raise ValueError(f"range end {actual_end} < start {start}")
        range_header = f"bytes={start}-{actual_end}"

        def _open() -> Iterable[bytes]:
            resp = self._client.get_object(
                Bucket=self.bucket, Key=key, Range=range_header
            )
            return resp["Body"].iter_chunks(chunk_size=64 * 1024)

        body_iter = await asyncio.to_thread(_open)

        async def _async_iter() -> AsyncIterator[bytes]:
            it = iter(body_iter)

            def _next() -> bytes | None:
                try:
                    return next(it)
                except StopIteration:
                    return None

            while True:
                chunk = await asyncio.to_thread(_next)
                if chunk is None:
                    break
                yield chunk

        return _async_iter(), meta, total

    # --- deletes -----------------------------------------------------------

    async def delete(self, key: str) -> None:
        def _do() -> None:
            self._client.delete_object(Bucket=self.bucket, Key=key)

        await asyncio.to_thread(_do)

    async def delete_prefix(self, prefix: str) -> int:
        """Bulk-delete every object whose key starts with ``prefix``. Returns
        the count actually deleted. Used by resource/image cascade.
        """

        def _do() -> int:
            paginator = self._client.get_paginator("list_objects_v2")
            total = 0
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                contents = page.get("Contents") or []
                if not contents:
                    continue
                # delete_objects supports up to 1000 keys per call.
                for i in range(0, len(contents), 1000):
                    batch = contents[i : i + 1000]
                    self._client.delete_objects(
                        Bucket=self.bucket,
                        Delete={"Objects": [{"Key": o["Key"]} for o in batch]},
                    )
                    total += len(batch)
            return total

        return await asyncio.to_thread(_do)


# --- module-level singleton wiring (set in lifespan) ------------------------

_store: BlobStore | None = None


def set_blob_store(store: BlobStore | None) -> None:
    global _store
    _store = store


def get_blob_store() -> BlobStore:
    if _store is None:
        raise RuntimeError("blob store not initialized")
    return _store
