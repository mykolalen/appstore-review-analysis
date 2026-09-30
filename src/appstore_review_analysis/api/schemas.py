"""HTTP request/response schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AnalysisRequest(BaseModel):
    """Create-analysis request."""

    app: str | int
    country: str = Field(default="us", min_length=2, max_length=2)
    sample_size: int = Field(default=100, ge=1, le=200)
    seed: int | None = Field(default=None, ge=0, lt=2**53)
    provider: Literal["itunes", "fixture", "rss"] | None = None
    window_days: int | None = Field(default=None, ge=1)
    analyze: bool = True


class MetricsResponse(BaseModel):
    """Metrics-oriented view of one stored analysis."""

    preprocessing: dict[str, object]
    metrics: dict[str, object]
    sentiment: dict[str, object]
    keywords: dict[str, object]


class InsightsResponse(BaseModel):
    """Insight-oriented view of themes and areas of improvement."""

    themes: dict[str, object]
    insights: dict[str, object]
