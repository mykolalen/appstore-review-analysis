"""Analysis pipeline producing deterministic, evidence-backed statistics and insights."""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from appstore_review_analysis.analysis.evidence import build_areas_of_improvement
from appstore_review_analysis.analysis.issue_categories import analyse_issue_categories
from appstore_review_analysis.analysis.keywords import analyse_keywords
from appstore_review_analysis.analysis.metrics import rating_metrics, rating_proportion_interval
from appstore_review_analysis.analysis.sentiment import SentimentAnalyzer, SentimentResult
from appstore_review_analysis.analysis.themes import (
    DEFAULT_DISTANCE_THRESHOLD,
    DEFAULT_UNIT_MIN_NEGATIVE_SCORE_OTHER,
    DEFAULT_UNIT_MIN_NEGATIVE_SCORE_POSITIVE_REVIEWS,
    ComplaintUnit,
    EmbeddingModel,
    analyse_themes,
)
from appstore_review_analysis.domain import (
    AnalysedReview,
    AnalysisPayload,
    CollectionResult,
    Review,
)
from appstore_review_analysis.errors import AppError
from appstore_review_analysis.text import ProcessedText, preprocess_review


@dataclass(frozen=True)
class DeadlineBudget:
    """Monotonic request budget shared by analysis stages."""

    started: float
    limit_s: float

    @property
    def elapsed_s(self) -> float:
        return time.monotonic() - self.started

    @property
    def remaining_s(self) -> float:
        return max(0.0, self.limit_s - self.elapsed_s)

    @property
    def expired(self) -> bool:
        return self.remaining_s <= 0.0


def analyse_collection(
    collection: CollectionResult,
    *,
    request_payload: dict[str, object],
    sentiment: SentimentAnalyzer | None,
    embedder: EmbeddingModel | None = None,
    analyze: bool,
    request_deadline_s: float,
    request_started: float | None = None,
    collection_ms: float | None = None,
    theme_distance_threshold: float = DEFAULT_DISTANCE_THRESHOLD,
    unit_min_negative_score_positive_reviews: float = (
        DEFAULT_UNIT_MIN_NEGATIVE_SCORE_POSITIVE_REVIEWS
    ),
    unit_min_negative_score_other: float = DEFAULT_UNIT_MIN_NEGATIVE_SCORE_OTHER,
) -> tuple[AnalysisPayload, list[AnalysedReview]]:
    """Run deterministic analysis and return response plus stored review rows."""

    started = request_started if request_started is not None else time.monotonic()
    budget = DeadlineBudget(started=started, limit_s=request_deadline_s)
    timings: dict[str, float] = {}

    preprocess_started = time.monotonic()
    preprocessed = [preprocess_review(review) for review in collection.reviews]
    timings["preprocess"] = _elapsed_ms(preprocess_started)

    metrics_started = time.monotonic()
    metrics = rating_metrics(collection.reviews, collection.sampling, collection.sampling.seed)
    timings["metrics"] = _elapsed_ms(metrics_started)
    if collection_ms is not None:
        timings["collection"] = collection_ms

    analysed_reviews = _base_rows(collection.reviews, preprocessed)
    reference_date = collection.sampling.collected_at

    if not analyze:
        sentiment_payload = _not_requested_sentiment(preprocessed)
        keywords: dict[str, object] = {
            "status": "not_requested",
            "common": [],
            "distinctive": [],
        }
        themes: dict[str, object] = {
            "status": "not_requested",
            "n_units": 0,
            "n_complaint_reviews": 0,
            "distance_threshold": theme_distance_threshold,
            "unit_gate": {
                "positive_review_min_negative_score": (unit_min_negative_score_positive_reviews),
                "other_review_min_negative_score": unit_min_negative_score_other,
                "negative_label_candidates": 0,
                "kept": 0,
                "removed_total": 0,
                "removed_by_rule": {
                    "rating_4_5_below_threshold": 0,
                    "rating_1_3_below_threshold": 0,
                },
            },
            "coverage": {
                "units_clustered": 0,
                "units_total": 0,
                "unit_share": None,
                "complaint_reviews_in_themes": 0,
                "n_complaint_reviews": 0,
                "review_share": None,
            },
            "diagnostics": {"pairwise_distance_histogram": [], "cluster_sizes": {}},
            "items": [],
            "other": {"unit_count": 0, "review_count": 0, "evidence_review_ids": []},
        }
        insights: dict[str, object] = {
            "status": "not_requested",
            "issue_categories": _empty_issue_categories("not_requested"),
            "areas_of_improvement": [],
        }
    else:
        if sentiment is None:
            raise _model_unavailable("Sentiment model is not available in this build/runtime.")
        if budget.expired:
            raise _deadline_before_sentiment()

        sentiment_started = time.monotonic()
        try:
            analysed_reviews, sentiment_payload = _run_sentiment(
                analysed_reviews,
                sentiment,
                population=collection.sampling.reachable,
            )
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                status_code=503,
                code="MODEL_UNAVAILABLE",
                message="Sentiment model inference failed.",
                details={"error": type(exc).__name__},
            ) from exc
        timings["sentiment"] = _elapsed_ms(sentiment_started)
        if budget.expired:
            raise AppError(
                status_code=504,
                code="ANALYSIS_DEADLINE_EXCEEDED",
                message="Request deadline was exceeded during sentiment analysis.",
            )

        if embedder is None:
            raise _model_unavailable("Embedding model is not available in this build/runtime.")

        keyword_started = time.monotonic()
        keywords = analyse_keywords(
            analysed_reviews,
            app_name=collection.app.name,
            reference_date=reference_date,
        )
        timings["keywords"] = _elapsed_ms(keyword_started)

        complaint_units: list[ComplaintUnit] = []
        if budget.expired:
            themes = {
                "status": "skipped_deadline",
                "n_units": 0,
                "n_complaint_reviews": 0,
                "distance_threshold": theme_distance_threshold,
                "unit_gate": {
                    "positive_review_min_negative_score": (
                        unit_min_negative_score_positive_reviews
                    ),
                    "other_review_min_negative_score": unit_min_negative_score_other,
                    "negative_label_candidates": 0,
                    "kept": 0,
                    "removed_total": 0,
                    "removed_by_rule": {
                        "rating_4_5_below_threshold": 0,
                        "rating_1_3_below_threshold": 0,
                    },
                },
                "coverage": {
                    "units_clustered": 0,
                    "units_total": 0,
                    "unit_share": None,
                    "complaint_reviews_in_themes": 0,
                    "n_complaint_reviews": 0,
                    "review_share": None,
                },
                "diagnostics": {"pairwise_distance_histogram": [], "cluster_sizes": {}},
                "items": [],
                "other": {"unit_count": 0, "review_count": 0, "evidence_review_ids": []},
            }
        else:
            try:
                themes, theme_timings, complaint_units = analyse_themes(
                    analysed_reviews,
                    sentiment=sentiment,
                    embedder=embedder,
                    reference_date=reference_date,
                    app_name=collection.app.name,
                    distance_threshold=theme_distance_threshold,
                    unit_min_negative_score_positive_reviews=(
                        unit_min_negative_score_positive_reviews
                    ),
                    unit_min_negative_score_other=unit_min_negative_score_other,
                )
                timings.update(theme_timings)
            except AppError:
                raise
            except Exception as exc:
                raise AppError(
                    status_code=503,
                    code="MODEL_UNAVAILABLE",
                    message="Embedding/theme analysis failed.",
                    details={"error": type(exc).__name__},
                ) from exc

        if budget.expired:
            issue_categories = _empty_issue_categories("skipped_deadline")
        else:
            issue_categories = analyse_issue_categories(
                complaint_units,
                reference_date=reference_date,
            )

        insights = build_areas_of_improvement(
            themes=themes,
            keywords=keywords,
            rows=analysed_reviews,
            issue_categories=issue_categories,
        )
    preprocessing = _preprocessing_summary(analysed_reviews)
    preprocessing.update(
        {
            "n_negative": _int_value(keywords.get("n_negative")),
            "n_rest": _int_value(keywords.get("n_rest")),
            "n_complaint_reviews": _int_value(themes.get("n_complaint_reviews")),
            "n_units": _int_value(themes.get("n_units")),
        }
    )

    analysis_complete = analyze and not budget.expired
    warnings = list(collection.warnings)
    if analyze and budget.expired:
        warnings.append("Request deadline reached after sentiment; later stages may be skipped.")

    payload = AnalysisPayload(
        analysis_id=str(uuid4()),
        created_at=datetime.now(UTC),
        app=collection.app,
        request=request_payload,
        sampling=collection.sampling,
        preprocessing=preprocessing,
        metrics=metrics,
        sentiment=sentiment_payload,
        keywords=keywords,
        themes=themes,
        insights=insights,
        provenance={
            "timings_ms": timings,
            "sentiment_model": _sentiment_provenance(sentiment_payload),
            "embedding_model": _embedding_provenance(embedder if analyze else None),
            "statistics": {
                "bootstrap_method": "BCa",
                "bootstrap_resamples": 9999,
                "seed": collection.sampling.seed,
            },
        },
        analysis_complete=analysis_complete,
        warnings=warnings,
    )
    return payload, analysed_reviews


def _empty_issue_categories(status: str) -> dict[str, object]:
    return {
        "status": status,
        "version": "1",
        "denominator": 0,
        "multi_label": True,
        "items": [],
        "not_categorised": {
            "review_count": 0,
            "denominator": 0,
            "share": None,
            "review_ids": [],
            "evidence": [],
        },
        "audit_rows": [],
    }


def _base_rows(reviews: list[Review], processed: list[ProcessedText]) -> list[AnalysedReview]:
    rows: list[AnalysedReview] = []
    for review, text in zip(reviews, processed, strict=True):
        rows.append(
            AnalysedReview(
                **review.model_dump(),
                analysis_text=text.analysis_text,
                lexical_text=text.lexical_text,
                language=text.language,
                analysable=text.analysable,
                flags=text.flags,
            )
        )
    return rows


def _run_sentiment(
    rows: list[AnalysedReview],
    analyzer: SentimentAnalyzer,
    *,
    population: int,
) -> tuple[list[AnalysedReview], dict[str, object]]:
    indexes = [index for index, row in enumerate(rows) if row.analysable]
    texts = [rows[index].analysis_text for index in indexes]
    predictions = analyzer.predict(texts)
    if len(predictions) != len(texts):
        raise _model_unavailable("Sentiment model returned an unexpected number of predictions.")

    model_id: str | None = None
    model_revision: str | None = None
    for index, prediction in zip(indexes, predictions, strict=True):
        rows[index] = _with_sentiment(rows[index], prediction)
        model_id = prediction.model_id
        model_revision = prediction.model_revision

    counts = Counter(row.sentiment_label for row in rows if row.sentiment_label is not None)
    n = sum(counts.values())
    distribution: dict[str, object] = {}
    census = bool(population and n == population)
    for label in ("negative", "neutral", "positive"):
        count = int(counts.get(label, 0))
        percentage = count / n if n else None
        distribution[label] = {
            "count": count,
            "percentage": percentage,
            "ci95": _label_ci(count, n, population=population, census=census),
        }

    comparable = 0
    agreements = 0
    disagreement: dict[str, int] = {
        "rating_negative_model_positive": 0,
        "rating_negative_model_neutral": 0,
        "rating_positive_model_negative": 0,
        "rating_positive_model_neutral": 0,
    }
    for row in rows:
        if row.sentiment_label is None or row.rating == 3:
            continue
        if row.rating <= 2:
            comparable += 1
            if row.sentiment_label == "negative":
                agreements += 1
            else:
                disagreement[f"rating_negative_model_{row.sentiment_label}"] += 1
        elif row.rating >= 4:
            comparable += 1
            if row.sentiment_label == "positive":
                agreements += 1
            else:
                disagreement[f"rating_positive_model_{row.sentiment_label}"] += 1

    return rows, {
        "status": "ok",
        "n_analysable": n,
        "distribution": distribution,
        "rating_consistency": {
            "comparable_reviews": comparable,
            "agreement_rate": (agreements / comparable if comparable else None),
            "disagreement": disagreement,
            "note": "Star ratings are a weak consistency proxy, not sentiment ground truth.",
        },
        "model_id": model_id,
        "model_revision": model_revision,
    }


def _with_sentiment(row: AnalysedReview, prediction: SentimentResult) -> AnalysedReview:
    flags = list(row.flags)
    if prediction.truncated and "truncated" not in flags:
        flags.append("truncated")
    return row.model_copy(
        update={
            "sentiment_label": prediction.label,
            "class_scores": dict(prediction.class_scores),
            "truncated": prediction.truncated,
            "flags": flags,
        }
    )


def _preprocessing_summary(rows: list[AnalysedReview]) -> dict[str, object]:
    flag_counts = Counter(flag for row in rows for flag in row.flags)
    language_counts = Counter(row.language for row in rows)
    return {
        "n_all": len(rows),
        "n_analysable": sum(row.analysable for row in rows),
        "flag_counts": dict(sorted(flag_counts.items())),
        "language_counts": dict(sorted(language_counts.items())),
        "examples": _preprocessing_examples(rows),
    }


def _preprocessing_examples(
    rows: list[AnalysedReview], *, limit: int = 5
) -> list[dict[str, object]]:
    if not rows or limit <= 0:
        return []
    ordered = sorted(
        rows,
        key=lambda row: (not bool(row.flags), row.source_review_id),
    )
    examples: list[dict[str, object]] = []
    for row in ordered[: min(limit, len(ordered))]:
        before = f"Title: {row.title} | Body: {row.body}"
        examples.append(
            {
                "review_id": row.source_review_id,
                "before": before,
                "after": row.analysis_text,
                "flags": list(row.flags),
            }
        )
    return examples


def _not_requested_sentiment(processed: list[ProcessedText]) -> dict[str, object]:
    return {
        "status": "not_requested",
        "n_analysable": sum(item.analysable for item in processed),
        "distribution": _empty_distribution("not_requested"),
        "rating_consistency": {
            "comparable_reviews": 0,
            "agreement_rate": None,
            "reason": "not_requested",
        },
        "model": None,
    }


def _empty_distribution(reason: str) -> dict[str, object]:
    return {
        label: {
            "count": 0,
            "percentage": None,
            "ci95": {"low": None, "high": None, "reason": reason},
        }
        for label in ("negative", "neutral", "positive")
    }


def _label_ci(
    successes: int,
    n: int,
    *,
    population: int,
    census: bool,
) -> dict[str, float | str | None]:
    return rating_proportion_interval(successes, n, population=population, census=census)


def _sentiment_provenance(payload: dict[str, object]) -> dict[str, object] | None:
    model_id = payload.get("model_id")
    if not model_id:
        return None
    return {"id": model_id, "revision": payload.get("model_revision")}


def _embedding_provenance(embedder: EmbeddingModel | None) -> dict[str, object] | None:
    if embedder is None:
        return None
    return {"id": embedder.model_id, "revision": embedder.model_revision}


def _int_value(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _model_unavailable(message: str) -> AppError:
    return AppError(status_code=503, code="MODEL_UNAVAILABLE", message=message)


def _deadline_before_sentiment() -> AppError:
    return AppError(
        status_code=504,
        code="ANALYSIS_DEADLINE_EXCEEDED",
        message="Request deadline was reached before sentiment analysis could run.",
    )


def _elapsed_ms(started: float) -> float:
    return round((time.monotonic() - started) * 1000.0, 3)
