"""Application configuration."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings."""

    log_level: str = "INFO"
    default_provider: str = "itunes"
    fixture_dir: Path = Path("data/fixtures")
    collection_concurrency: int = Field(default=4, ge=1, le=16)
    collection_timeout_s: float = Field(default=10.0, gt=0)
    collection_deadline_s: float = Field(default=45.0, gt=0)
    collection_lock_wait_s: float = Field(default=10.0, ge=0)
    collection_max_replacements: int = Field(default=20, ge=0, le=100)
    http_max_retries: int = Field(default=3, ge=0, le=10)
    rss_empty_retries: int = Field(default=3, ge=0, le=10)
    database_url: str = "sqlite:///data/app.db"
    request_deadline_s: float = Field(default=90.0, gt=0)
    models_dir: Path = Path("models")
    theme_distance_threshold: float = Field(default=0.40, gt=0.0, le=2.0)
    unit_min_negative_score_positive_reviews: float = Field(default=0.85, ge=0.0, le=1.0)
    unit_min_negative_score_other: float = Field(default=0.50, ge=0.0, le=1.0)
    public_mode: bool = False
    public_max_sample_size: int = Field(default=100, ge=1, le=200)
    public_global_post_limit: int = Field(default=10, ge=1, le=1000)
    public_client_post_limit: int = Field(default=5, ge=1, le=1000)
    public_rate_window_s: float = Field(default=600.0, gt=0)
    public_seed_analysis_path: Path = Path("reports/nebula_us_seed42.analysis.json")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide default settings object."""

    return Settings()
