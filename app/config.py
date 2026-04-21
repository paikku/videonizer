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

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def job_timeout_s(self) -> float:
        return self.job_timeout_ms / 1000.0

    @property
    def ffmpeg_extra_args_list(self) -> list[str]:
        return shlex.split(self.ffmpeg_extra_args) if self.ffmpeg_extra_args else []


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
