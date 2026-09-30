"""FastAPI application factory."""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request, Response

from appstore_review_analysis import __version__
from appstore_review_analysis.analysis.sentiment import SentimentAnalyzer, TransformerSentiment
from appstore_review_analysis.analysis.themes import (
    EmbeddingModel,
    FakeEmbedder,
    SentenceTransformerEmbedder,
)
from appstore_review_analysis.api.routes import router
from appstore_review_analysis.config import Settings, get_settings
from appstore_review_analysis.errors import install_exception_handlers
from appstore_review_analysis.logging import configure_logging
from appstore_review_analysis.public_mode import (
    PublicRateLimiter,
    load_public_seed_analysis,
    public_seed_rows,
)
from appstore_review_analysis.storage import StorageRepository


def _configure_stdio() -> None:
    """Make console output UTF-8 even on Windows code pages such as cp1251."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="backslashreplace")


def create_app(
    settings: Settings | None = None,
    *,
    sentiment: SentimentAnalyzer | None = None,
    embedder: EmbeddingModel | None = None,
) -> FastAPI:
    """Create an application with injectable model dependencies for tests."""

    _configure_stdio()
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        repository = StorageRepository(resolved_settings.database_url)
        repository.create_schema()
        app.state.repository = repository
        if resolved_settings.public_mode:
            seed_analysis = load_public_seed_analysis(resolved_settings.public_seed_analysis_path)
            repository.upsert_seed_analysis(
                seed_analysis, public_seed_rows(seed_analysis, resolved_settings.fixture_dir)
            )
            app.state.public_limiter = PublicRateLimiter(
                global_capacity=resolved_settings.public_global_post_limit,
                client_capacity=resolved_settings.public_client_post_limit,
                window_s=resolved_settings.public_rate_window_s,
            )
        else:
            app.state.public_limiter = None
        app.state.sentiment_load_error = None
        app.state.embedder_load_error = None

        resolved_sentiment = sentiment
        if resolved_sentiment is None:
            try:
                resolved_sentiment = TransformerSentiment.load(resolved_settings.models_dir)
            except Exception as exc:  # startup readiness records the failure; process stays alive
                app.state.sentiment_load_error = type(exc).__name__
                resolved_sentiment = None
        app.state.sentiment = resolved_sentiment

        resolved_embedder = embedder
        if resolved_embedder is None and sentiment is not None:
            # Model injection marks an offline/test app; keep injected test applications
            # network-free by pairing them with the deterministic fake embedder.
            resolved_embedder = FakeEmbedder()
        elif resolved_embedder is None:
            try:
                resolved_embedder = SentenceTransformerEmbedder.load(resolved_settings.models_dir)
            except Exception as exc:
                app.state.embedder_load_error = type(exc).__name__
                resolved_embedder = None
        app.state.embedder = resolved_embedder

        try:
            yield
        finally:
            repository.close()

    app = FastAPI(
        title="App Store Review Analysis API",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings

    @app.middleware("http")
    async def request_id_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        supplied = request.headers.get("X-Request-ID", "").strip()
        request_id = supplied or str(uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    install_exception_handlers(app)
    app.include_router(router)
    return app


app = create_app()
