"""HTTP routes for collection, analysis, insights and review export."""

from __future__ import annotations

import time
from typing import Literal, cast

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from appstore_review_analysis.analysis.pipeline import analyse_collection
from appstore_review_analysis.analysis.sentiment import SentimentAnalyzer
from appstore_review_analysis.analysis.themes import EmbeddingModel
from appstore_review_analysis.api.schemas import AnalysisRequest, InsightsResponse, MetricsResponse
from appstore_review_analysis.collection.fixture import FixtureProvider
from appstore_review_analysis.collection.itunes import ITunesProvider
from appstore_review_analysis.collection.rss import RSSProvider
from appstore_review_analysis.collection.sampling import (
    parse_app_id,
    resolve_seed,
    validate_sample_size,
)
from appstore_review_analysis.collection.storefronts import normalise_country
from appstore_review_analysis.config import Settings
from appstore_review_analysis.domain import AnalysisPayload
from appstore_review_analysis.errors import AppError
from appstore_review_analysis.export import analysed_review_json_row, render_analysed_reviews_csv
from appstore_review_analysis.public_mode import PublicRateLimiter, public_client_key
from appstore_review_analysis.storage import StorageRepository

router = APIRouter()


class HealthResponse(BaseModel):
    """Liveness response."""

    status: Literal["ok"] = "ok"


@router.get("/healthz", response_model=HealthResponse, tags=["health"])
def healthz() -> HealthResponse:
    """Return process liveness."""

    return HealthResponse()


@router.get("/readyz", response_model=HealthResponse, tags=["health"])
def readyz(request: Request) -> HealthResponse:
    """Return readiness only when the database and sentiment model are available."""

    _repository(request)
    if _sentiment(request) is None:
        raise AppError(
            status_code=503,
            code="MODEL_UNAVAILABLE",
            message="Sentiment model is not available.",
            details={
                "load_error": getattr(request.app.state, "sentiment_load_error", None),
            },
        )
    if _embedder(request) is None:
        raise AppError(
            status_code=503,
            code="MODEL_UNAVAILABLE",
            message="Embedding model is not available.",
            details={
                "load_error": getattr(request.app.state, "embedder_load_error", None),
            },
        )
    return HealthResponse()


@router.post(
    "/v1/analyses",
    response_model=AnalysisPayload,
    status_code=201,
    tags=["collection"],
    summary="Collect reviews for an app and analyse them",
)
def create_analysis(
    payload: AnalysisRequest,
    request: Request,
    response: Response,
) -> AnalysisPayload:
    """Collect one reproducible sample, analyse it and persist the result."""

    settings = _settings(request)
    started = time.monotonic()
    app_id = parse_app_id(payload.app)
    country = normalise_country(payload.country)
    sample_size = validate_sample_size(payload.sample_size)
    seed = resolve_seed(payload.seed)
    provider_name = payload.provider or (
        "fixture" if settings.public_mode else settings.default_provider
    )

    if payload.window_days is not None and provider_name != "itunes":
        raise AppError(
            status_code=422,
            code="INVALID_INPUT",
            message="window_days is supported only by provider=itunes.",
            details={"provider": provider_name, "window_days": payload.window_days},
        )

    if settings.public_mode:
        if sample_size > settings.public_max_sample_size:
            raise AppError(
                status_code=422,
                code="INVALID_INPUT",
                message=f"sample_size must be <= {settings.public_max_sample_size} in public mode.",
                details={"sample_size": sample_size},
            )
        if provider_name == "rss":
            raise AppError(
                status_code=422,
                code="INVALID_INPUT",
                message="provider=rss is disabled in public mode.",
                details={"provider": provider_name},
            )
        limiter = _public_limiter(request)
        allowed, retry_after = limiter.consume(public_client_key(request))
        if not allowed:
            raise AppError(
                status_code=429,
                code="RATE_LIMITED",
                message="Public demo POST rate limit exceeded.",
                retry_after=retry_after,
            )

    provider = _provider(provider_name, settings)
    try:
        collection_started = time.monotonic()
        if isinstance(provider, ITunesProvider):
            collection = provider.sample(
                app_id, country, sample_size, seed, window_days=payload.window_days
            )
        else:
            collection = provider.sample(app_id, country, sample_size, seed)
        collection_ms = round((time.monotonic() - collection_started) * 1000.0, 3)
    finally:
        if isinstance(provider, (ITunesProvider, RSSProvider)):
            provider.close()

    request_payload: dict[str, object] = {
        "app": str(payload.app),
        "country": country,
        "sample_size": sample_size,
        "seed": seed,
        "provider": provider_name,
        "window_days": payload.window_days,
        "analyze": payload.analyze,
    }
    analysis, rows = analyse_collection(
        collection,
        request_payload=request_payload,
        sentiment=_sentiment(request),
        embedder=_embedder(request),
        analyze=payload.analyze,
        request_deadline_s=settings.request_deadline_s,
        request_started=started,
        collection_ms=collection_ms,
        theme_distance_threshold=settings.theme_distance_threshold,
        unit_min_negative_score_positive_reviews=(
            settings.unit_min_negative_score_positive_reviews
        ),
        unit_min_negative_score_other=settings.unit_min_negative_score_other,
    )
    _repository(request).save_analysis(analysis, rows)
    response.headers["Location"] = f"/v1/analyses/{analysis.analysis_id}"
    return analysis


@router.get("/v1/analyses/{analysis_id}", response_model=AnalysisPayload, tags=["analysis"])
def get_analysis(analysis_id: str, request: Request) -> AnalysisPayload:
    """Return a stored analysis."""

    return _require_analysis(_repository(request), analysis_id)


@router.get(
    "/v1/analyses/{analysis_id}/metrics",
    response_model=MetricsResponse,
    tags=["analysis"],
)
def get_metrics(analysis_id: str, request: Request) -> MetricsResponse:
    """Return metrics/preprocessing/sentiment view."""

    analysis = _require_analysis(_repository(request), analysis_id)
    return MetricsResponse(
        preprocessing=analysis.preprocessing,
        metrics=analysis.metrics,
        sentiment=analysis.sentiment,
        keywords=analysis.keywords,
    )


@router.get(
    "/v1/analyses/{analysis_id}/insights",
    response_model=InsightsResponse,
    tags=["analysis"],
)
def get_insights(analysis_id: str, request: Request) -> InsightsResponse:
    """Return the stored themes and evidence-backed insights view."""

    analysis = _require_analysis(_repository(request), analysis_id)
    return InsightsResponse(themes=analysis.themes, insights=analysis.insights)


@router.get("/v1/analyses/{analysis_id}/reviews", tags=["analysis"])
def download_reviews(
    analysis_id: str,
    request: Request,
    format_: Literal["csv", "json"] = Query(default="json", alias="format"),
    excel: bool = False,
    delimiter: str = ",",
) -> Response:
    """Download the stored allowlisted review sample as JSON or CSV."""

    repository = _repository(request)
    _require_analysis(repository, analysis_id)
    rows = repository.get_reviews(analysis_id)
    if format_ == "json":
        return JSONResponse(content=[analysed_review_json_row(row) for row in rows])
    if delimiter not in {",", ";"}:
        raise AppError(
            status_code=422,
            code="INVALID_INPUT",
            message="delimiter must be ',' or ';'.",
            details={"delimiter": delimiter},
        )
    content = render_analysed_reviews_csv(rows, excel=excel, delimiter=delimiter)
    return Response(
        content=content,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="reviews-{analysis_id}.csv"',
        },
    )


def _provider(name: str, settings: Settings) -> ITunesProvider | FixtureProvider | RSSProvider:
    if name == "itunes":
        return ITunesProvider(settings)
    if name == "fixture":
        return FixtureProvider(settings.fixture_dir)
    if name == "rss":
        return RSSProvider(settings)
    raise AppError(
        status_code=422,
        code="INVALID_INPUT",
        message="provider must be 'itunes', 'fixture', or 'rss'.",
        details={"provider": name},
    )


def _public_limiter(request: Request) -> PublicRateLimiter:
    limiter = getattr(request.app.state, "public_limiter", None)
    if not isinstance(limiter, PublicRateLimiter):
        raise AppError(
            status_code=503,
            code="PUBLIC_MODE_UNAVAILABLE",
            message="Public-mode rate limiter is not initialized.",
        )
    return limiter


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def _repository(request: Request) -> StorageRepository:
    return cast(StorageRepository, request.app.state.repository)


def _sentiment(request: Request) -> SentimentAnalyzer | None:
    return cast(SentimentAnalyzer | None, request.app.state.sentiment)


def _embedder(request: Request) -> EmbeddingModel | None:
    return cast(EmbeddingModel | None, request.app.state.embedder)


def _require_analysis(repository: StorageRepository, analysis_id: str) -> AnalysisPayload:
    analysis = repository.get_analysis(analysis_id)
    if analysis is None:
        raise AppError(
            status_code=404,
            code="ANALYSIS_NOT_FOUND",
            message="Analysis was not found.",
            details={"analysis_id": analysis_id},
        )
    return analysis
