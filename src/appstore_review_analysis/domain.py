"""Domain models shared by collection, export, CLI and analysis."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class AppInfo(BaseModel):
    """App metadata required by the take-home."""

    app_id: int
    name: str
    country: str
    average_user_rating: float | None = None
    user_rating_count: int | None = None
    current_version_release_date: datetime | None = None


class PopulationInfo(BaseModel):
    """Written-review population and store-level rating benchmark."""

    total_written_reviews: int
    rating_count: int
    rating_count_list: list[int] = Field(min_length=5, max_length=5)
    store_mean: float | None
    reachable: int
    frame_first_date: datetime | None = None
    frame_last_date: datetime | None = None


class Review(BaseModel):
    """Privacy-minimised review representation."""

    source_review_id: str
    rank: int | None
    country: str
    title: str
    body: str
    rating: int = Field(ge=1, le=5)
    created_at: datetime | None
    is_edited: bool | None
    vote_count: int | None
    vote_sum: int | None
    has_developer_response: bool
    developer_response_id: str | None
    developer_response_date: datetime | None
    app_version: str | None = None
    provider: str
    source_flags: list[str] = Field(default_factory=list)


class SamplingMetadata(BaseModel):
    """Provenance for one collection."""

    provider: str
    method: Literal["uniform_random_rank", "uniform_random_rss_frame", "fixture_replay"]
    storefront: str
    population_total: int
    reachable: int
    frame_description: str
    window_days: int | None = Field(
        default=None,
        ge=1,
        exclude_if=lambda value: value is None,
    )
    frame_first_date: datetime | None
    frame_last_date: datetime | None
    requested: int
    actual: int
    sample_complete: bool
    sampling_fraction: float | None
    seed: int
    ranks: list[int]
    replacements: list[dict[str, int | str]] = Field(default_factory=list)
    rows_invalid: int = 0
    collected_at: datetime
    requests_made: int = 0
    retries: int = 0
    store_histogram: list[int] = Field(default_factory=list)
    store_mean: float | None = None
    store_rating_count: int = 0


class CollectionResult(BaseModel):
    """Complete output of the collection stage."""

    app: AppInfo
    sampling: SamplingMetadata
    reviews: list[Review]
    warnings: list[str] = Field(default_factory=list)


class AnalysedReview(Review):
    """Review plus derived text/language/sentiment fields stored for one analysis."""

    analysis_text: str
    lexical_text: str
    language: str
    analysable: bool
    flags: list[str] = Field(default_factory=list)
    sentiment_label: Literal["negative", "neutral", "positive"] | None = None
    class_scores: dict[str, float] = Field(default_factory=dict)
    truncated: bool = False


class AnalysisPayload(BaseModel):
    """Persisted and API-visible result for one analysis."""

    analysis_id: str
    created_at: datetime
    app: AppInfo
    request: dict[str, object]
    sampling: SamplingMetadata
    preprocessing: dict[str, object]
    metrics: dict[str, object]
    sentiment: dict[str, object]
    keywords: dict[str, object]
    themes: dict[str, object]
    insights: dict[str, object]
    provenance: dict[str, object]
    analysis_complete: bool
    warnings: list[str] = Field(default_factory=list)
