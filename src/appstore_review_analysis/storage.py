"""Synchronous SQLAlchemy persistence for completed analyses."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    delete,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Engine

from appstore_review_analysis.domain import AnalysedReview, AnalysisPayload

metadata = MetaData()

analyses = Table(
    "analyses",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("created_at", String(64), nullable=False),
    Column("app_id", Integer, nullable=False),
    Column("app_name", Text, nullable=False),
    Column("country", String(2), nullable=False),
    Column("provider", String(32), nullable=False),
    Column("app_json", JSON, nullable=False),
    Column("request_json", JSON, nullable=False),
    Column("sampling_json", JSON, nullable=False),
    Column("preprocessing_json", JSON, nullable=False),
    Column("metrics_json", JSON, nullable=False),
    Column("sentiment_json", JSON, nullable=False),
    Column("keywords_json", JSON, nullable=False),
    Column("themes_json", JSON, nullable=False),
    Column("insights_json", JSON, nullable=False),
    Column("provenance_json", JSON, nullable=False),
    Column("analysis_complete", Boolean, nullable=False),
    Column("warnings_json", JSON, nullable=False),
)

reviews = Table(
    "reviews",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("analysis_id", String(36), ForeignKey("analyses.id"), nullable=False, index=True),
    Column("position", Integer, nullable=False),
    Column("source_review_id", String(128), nullable=False),
    Column("rank", Integer, nullable=True),
    Column("country", String(2), nullable=False),
    Column("title", Text, nullable=False),
    Column("body", Text, nullable=False),
    Column("rating", Integer, nullable=False),
    Column("created_at", String(64), nullable=True),
    Column("is_edited", Boolean, nullable=True),
    Column("vote_count", Integer, nullable=True),
    Column("vote_sum", Integer, nullable=True),
    Column("has_developer_response", Boolean, nullable=False),
    Column("developer_response_id", String(128), nullable=True),
    Column("developer_response_date", String(64), nullable=True),
    Column("app_version", String(128), nullable=True),
    Column("provider", String(32), nullable=False),
    Column("source_flags", JSON, nullable=False),
    Column("analysis_text", Text, nullable=False),
    Column("lexical_text", Text, nullable=False),
    Column("language", String(16), nullable=False),
    Column("analysable", Boolean, nullable=False),
    Column("flags", JSON, nullable=False),
    Column("sentiment_label", String(16), nullable=True),
    Column("class_scores", JSON, nullable=False),
    Column("truncated", Boolean, nullable=False),
)


class StorageRepository:
    """Small repository with one insert-only transaction per completed analysis."""

    def __init__(self, database_url: str) -> None:
        _ensure_sqlite_parent(database_url)
        self.engine: Engine = create_engine(database_url)

    def create_schema(self) -> None:
        metadata.create_all(self.engine)

    def close(self) -> None:
        self.engine.dispose()

    def save_analysis(self, payload: AnalysisPayload, rows: list[AnalysedReview]) -> None:
        analysis_row = self._analysis_row(payload)
        review_rows = [
            self._review_row(payload.analysis_id, index, row) for index, row in enumerate(rows)
        ]
        with self.engine.begin() as connection:
            connection.execute(insert(analyses), analysis_row)
            if review_rows:
                connection.execute(insert(reviews), review_rows)

    def upsert_seed_analysis(
        self, payload: AnalysisPayload, rows: list[AnalysedReview] | None = None
    ) -> None:
        """Upsert only the immutable public-demo seed under its deterministic id."""

        analysis_row = self._analysis_row(payload)
        review_rows = [
            self._review_row(payload.analysis_id, index, row)
            for index, row in enumerate(rows or [])
        ]
        with self.engine.begin() as connection:
            exists = connection.execute(
                select(analyses.c.id).where(analyses.c.id == payload.analysis_id)
            ).first()
            if exists is None:
                connection.execute(insert(analyses), analysis_row)
            else:
                connection.execute(
                    update(analyses)
                    .where(analyses.c.id == payload.analysis_id)
                    .values(**analysis_row)
                )
                connection.execute(
                    delete(reviews).where(reviews.c.analysis_id == payload.analysis_id)
                )
            if review_rows:
                connection.execute(insert(reviews), review_rows)

    @staticmethod
    def _analysis_row(payload: AnalysisPayload) -> dict[str, Any]:
        data = payload.model_dump(mode="json")
        return {
            "id": payload.analysis_id,
            "created_at": data["created_at"],
            "app_id": payload.app.app_id,
            "app_name": payload.app.name,
            "country": payload.app.country,
            "provider": payload.sampling.provider,
            "app_json": data["app"],
            "request_json": data["request"],
            "sampling_json": data["sampling"],
            "preprocessing_json": data["preprocessing"],
            "metrics_json": data["metrics"],
            "sentiment_json": data["sentiment"],
            "keywords_json": data["keywords"],
            "themes_json": data["themes"],
            "insights_json": data["insights"],
            "provenance_json": data["provenance"],
            "analysis_complete": payload.analysis_complete,
            "warnings_json": list(payload.warnings),
        }

    def get_analysis(self, analysis_id: str) -> AnalysisPayload | None:
        statement = select(analyses).where(analyses.c.id == analysis_id)
        with self.engine.connect() as connection:
            row = connection.execute(statement).mappings().first()
        if row is None:
            return None
        return AnalysisPayload.model_validate(
            {
                "analysis_id": row["id"],
                "created_at": row["created_at"],
                "app": row["app_json"],
                "request": row["request_json"],
                "sampling": row["sampling_json"],
                "preprocessing": row["preprocessing_json"],
                "metrics": row["metrics_json"],
                "sentiment": row["sentiment_json"],
                "keywords": row["keywords_json"],
                "themes": row["themes_json"],
                "insights": row["insights_json"],
                "provenance": row["provenance_json"],
                "analysis_complete": row["analysis_complete"],
                "warnings": row["warnings_json"],
            }
        )

    def get_reviews(self, analysis_id: str) -> list[AnalysedReview]:
        statement = (
            select(reviews).where(reviews.c.analysis_id == analysis_id).order_by(reviews.c.position)
        )
        with self.engine.connect() as connection:
            stored = connection.execute(statement).mappings().all()
        output: list[AnalysedReview] = []
        for row in stored:
            output.append(
                AnalysedReview.model_validate(
                    {
                        "source_review_id": row["source_review_id"],
                        "rank": row["rank"],
                        "country": row["country"],
                        "title": row["title"],
                        "body": row["body"],
                        "rating": row["rating"],
                        "created_at": row["created_at"],
                        "is_edited": row["is_edited"],
                        "vote_count": row["vote_count"],
                        "vote_sum": row["vote_sum"],
                        "has_developer_response": row["has_developer_response"],
                        "developer_response_id": row["developer_response_id"],
                        "developer_response_date": row["developer_response_date"],
                        "app_version": row["app_version"],
                        "provider": row["provider"],
                        "source_flags": row["source_flags"],
                        "analysis_text": row["analysis_text"],
                        "lexical_text": row["lexical_text"],
                        "language": row["language"],
                        "analysable": row["analysable"],
                        "flags": row["flags"],
                        "sentiment_label": row["sentiment_label"],
                        "class_scores": row["class_scores"],
                        "truncated": row["truncated"],
                    }
                )
            )
        return output

    @staticmethod
    def _review_row(analysis_id: str, position: int, row: AnalysedReview) -> dict[str, Any]:
        data = row.model_dump(mode="json")
        return {
            "analysis_id": analysis_id,
            "position": position,
            "source_review_id": row.source_review_id,
            "rank": row.rank,
            "country": row.country,
            "title": row.title,
            "body": row.body,
            "rating": row.rating,
            "created_at": data["created_at"],
            "is_edited": row.is_edited,
            "vote_count": row.vote_count,
            "vote_sum": row.vote_sum,
            "has_developer_response": row.has_developer_response,
            "developer_response_id": row.developer_response_id,
            "developer_response_date": data["developer_response_date"],
            "app_version": row.app_version,
            "provider": row.provider,
            "source_flags": list(row.source_flags),
            "analysis_text": row.analysis_text,
            "lexical_text": row.lexical_text,
            "language": row.language,
            "analysable": row.analysable,
            "flags": list(row.flags),
            "sentiment_label": row.sentiment_label,
            "class_scores": dict(row.class_scores),
            "truncated": row.truncated,
        }


def _ensure_sqlite_parent(database_url: str) -> None:
    prefixes = ("sqlite:///", "sqlite+pysqlite:///")
    prefix = next((item for item in prefixes if database_url.startswith(item)), None)
    if prefix is None:
        return
    database = database_url[len(prefix) :]
    if database in {"", ":memory:"}:
        return
    Path(database).expanduser().parent.mkdir(parents=True, exist_ok=True)
