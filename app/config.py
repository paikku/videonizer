from __future__ import annotations

import os
import shlex
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_concurrency() -> int:
    return os.cpu_count() or 2


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    port: int = 8080
    max_upload_bytes: int = 2 * 1024 * 1024 * 1024
    max_concurrent_jobs: int = _default_concurrency()
    job_timeout_ms: int = 600_000
    allowed_origins: str = ""
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    ffmpeg_extra_args: str = ""
    temp_dir: str | None = None
    log_level: str = "INFO"

    # --- Segmentation (/v1/segment) -----------------------------------------
    # CPU-only inference → keep concurrency tight to bound RAM/CPU.
    segment_max_concurrent: int = 2
    # Per-request wall-clock budget. SAM/Mask R-CNN on CPU can take a few
    # seconds; cap so a runaway request doesn't pile up.
    segment_timeout_ms: int = 30_000
    # Maximum number of requests allowed to queue waiting for a worker slot.
    # Beyond this we shed load with 503 + Retry-After so a burst doesn't push
    # the proxy past its own timeout while requests sit forever in the queue.
    # Set to 0 to disable the cap (legacy behavior — not recommended).
    segment_max_queue: int = 16
    # How long a request may wait for a worker slot before we abandon it
    # with 504. Independent of `segment_timeout_ms`, which only starts
    # counting once inference has begun.
    segment_acquire_timeout_ms: int = 10_000
    # Cap upload size for /v1/segment (single frame, much smaller than video).
    segment_max_upload_bytes: int = 16 * 1024 * 1024
    # Crop padding around the prompt bbox before inference (fraction of bbox).
    segment_crop_padding: float = 0.20
    # Douglas-Peucker tolerance in normalized coords.
    segment_polygon_epsilon: float = 0.002
    # Where on disk to look for pre-downloaded model weights. Empty = let the
    # backend resolve through its own default cache.
    segment_weights_dir: str = ""
    # Eagerly load these backends at startup (comma-separated public model ids).
    segment_preload_models: str = ""

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def job_timeout_s(self) -> float:
        return self.job_timeout_ms / 1000.0

    @property
    def ffmpeg_extra_args_list(self) -> list[str]:
        return shlex.split(self.ffmpeg_extra_args) if self.ffmpeg_extra_args else []

    @property
    def segment_timeout_s(self) -> float:
        return self.segment_timeout_ms / 1000.0

    @property
    def segment_acquire_timeout_s(self) -> float:
        return self.segment_acquire_timeout_ms / 1000.0

    @property
    def segment_preload_models_list(self) -> list[str]:
        return [
            m.strip()
            for m in self.segment_preload_models.split(",")
            if m.strip()
        ]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
