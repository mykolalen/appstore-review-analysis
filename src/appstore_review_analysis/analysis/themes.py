"""Complaint-sentence extraction, MiniLM embeddings and adaptive semantic clustering."""

from __future__ import annotations

import os
import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from huggingface_hub import snapshot_download
from numpy.typing import NDArray
from sklearn.cluster import AgglomerativeClustering

from appstore_review_analysis.analysis.keywords import distinctive_phrases_for_texts
from appstore_review_analysis.analysis.metrics import wilson_interval
from appstore_review_analysis.analysis.sentiment import MODEL_INFERENCE_LOCK, SentimentAnalyzer
from appstore_review_analysis.domain import AnalysedReview
from appstore_review_analysis.hf_download import suppress_public_download_auth_warning
from appstore_review_analysis.text import lexical_text, normalise_text

EMBEDDING_MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_MODEL_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"  # pragma: allowlist secret
EMBEDDING_MODEL_DIRNAME = "sentence-transformers--all-MiniLM-L6-v2"
EMBEDDING_MODEL_ALLOW_PATTERNS = [
    "config*.json",
    "modules.json",
    "model.safetensors",
    "sentence_bert_config.json",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.txt",
    "1_Pooling/*",
]
DEFAULT_DISTANCE_THRESHOLD = 0.40
DEFAULT_UNIT_MIN_NEGATIVE_SCORE_POSITIVE_REVIEWS = 0.85
DEFAULT_UNIT_MIN_NEGATIVE_SCORE_OTHER = 0.50
DIAGNOSTIC_THRESHOLDS = (0.30, 0.40, 0.50, 0.60)
# The evaluation pair strata span [0.20, 0.65); a 0.05 grid covers that range without
# selecting a threshold from the demo sample itself.
THRESHOLD_TUNING_GRID = tuple(round(0.20 + 0.05 * index, 2) for index in range(10))
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|[\r\n]+")
_DISTANCE_HISTOGRAM_EDGES = (0.0, 0.20, 0.30, 0.40, 0.50, 0.60, 0.80, 1.00, 2.00)


class EmbeddingModel(Protocol):
    """Small protocol so tests never need a real Sentence Transformer."""

    model_id: str
    model_revision: str

    def encode(self, texts: list[str]) -> NDArray[np.float64]: ...


class FakeEmbedder:
    """Fast deterministic embedder for tests and injected-model application runs."""

    model_id = "fake-embedder"
    model_revision = "test"

    def encode(self, texts: list[str]) -> NDArray[np.float64]:
        vectors: list[list[float]] = []
        for text in texts:
            lower = text.lower()
            vector = np.asarray(
                [
                    _hits(
                        lower,
                        ("charge", "charged", "refund", "subscription", "trial", "billing"),
                    ),
                    _hits(lower, ("crash", "bug", "slow", "freeze", "broken", "work")),
                    _hits(lower, ("content", "reading", "accurate", "advice", "prediction")),
                    _hits(lower, ("cancel", "delete", "account", "login", "password")),
                    1.0,
                ],
                dtype=np.float64,
            )
            norm = float(np.linalg.norm(vector))
            vectors.append((vector / norm if norm else vector).tolist())
        return np.asarray(vectors, dtype=np.float64)


class SentenceTransformerEmbedder:
    """Pinned MiniLM sentence embedder loaded entirely from MODELS_DIR."""

    model_id = EMBEDDING_MODEL_ID
    model_revision = EMBEDDING_MODEL_REVISION

    def __init__(self, model: Any, *, batch_size: int = 32) -> None:
        self.model = model
        self.batch_size = batch_size

    @classmethod
    def load(cls, models_dir: Path) -> SentenceTransformerEmbedder:
        _configure_offline_environment()
        path = embedding_model_path(models_dir)
        if not path.exists():
            raise FileNotFoundError(
                f"Embedding model is not downloaded at {path}. Run 'reviews download-models'."
            )
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(str(path), device="cpu", local_files_only=True)
        return cls(model)

    def encode(self, texts: list[str]) -> NDArray[np.float64]:
        if not texts:
            return np.empty((0, 0), dtype=np.float64)
        with MODEL_INFERENCE_LOCK:
            vectors = self.model.encode(
                texts,
                batch_size=self.batch_size,
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
        return np.asarray(vectors, dtype=np.float64)


@dataclass(frozen=True)
class ComplaintUnit:
    """One model-negative sentence tied back to its source review."""

    unit_id: str
    review_id: str
    text: str
    lexical_text: str
    rating: int
    created_at: datetime | None
    negative_score: float


@dataclass(frozen=True)
class UnitGateResult:
    """Complaint units plus transparent score-gate accounting."""

    units: list[ComplaintUnit]
    negative_label_candidates: int
    removed_positive_reviews: int
    removed_other_reviews: int
    positive_review_threshold: float
    other_threshold: float

    def as_dict(self) -> dict[str, object]:
        return {
            "positive_review_min_negative_score": self.positive_review_threshold,
            "other_review_min_negative_score": self.other_threshold,
            "negative_label_candidates": self.negative_label_candidates,
            "kept": len(self.units),
            "removed_total": self.removed_positive_reviews + self.removed_other_reviews,
            "removed_by_rule": {
                "rating_4_5_below_threshold": self.removed_positive_reviews,
                "rating_1_3_below_threshold": self.removed_other_reviews,
            },
            "note": (
                "Default score gates are heuristics based on observed classifier errors and "
                "must be re-tuned on hand-labelled complaint units when evaluation data exists."
            ),
        }


def embedding_model_path(models_dir: Path) -> Path:
    return models_dir / EMBEDDING_MODEL_DIRNAME


def download_embedding_model(models_dir: Path) -> Path:
    """Download only the files required for offline MiniLM inference."""

    os.environ["DISABLE_SAFETENSORS_CONVERSION"] = "1"
    target = embedding_model_path(models_dir)
    target.mkdir(parents=True, exist_ok=True)
    with suppress_public_download_auth_warning():
        snapshot_download(
            repo_id=EMBEDDING_MODEL_ID,
            revision=EMBEDDING_MODEL_REVISION,
            local_dir=target,
            allow_patterns=EMBEDDING_MODEL_ALLOW_PATTERNS,
        )
    return target


def analyse_themes(
    rows: list[AnalysedReview],
    *,
    sentiment: SentimentAnalyzer,
    embedder: EmbeddingModel,
    reference_date: datetime,
    app_name: str = "",
    distance_threshold: float = DEFAULT_DISTANCE_THRESHOLD,
    unit_min_negative_score_positive_reviews: float = (
        DEFAULT_UNIT_MIN_NEGATIVE_SCORE_POSITIVE_REVIEWS
    ),
    unit_min_negative_score_other: float = DEFAULT_UNIT_MIN_NEGATIVE_SCORE_OTHER,
) -> tuple[dict[str, object], dict[str, float]]:
    """Extract complaint sentences and cluster them without forcing a topic count."""

    _validate_unit_threshold(unit_min_negative_score_positive_reviews)
    _validate_unit_threshold(unit_min_negative_score_other)
    _validate_distance_threshold(distance_threshold)

    timings: dict[str, float] = {}
    unit_started = time.monotonic()
    gate = complaint_units(
        rows,
        sentiment,
        positive_review_threshold=unit_min_negative_score_positive_reviews,
        other_threshold=unit_min_negative_score_other,
    )
    units = gate.units
    timings["units"] = _elapsed_ms(unit_started)
    complaint_review_ids = sorted({unit.review_id for unit in units})
    n_analysable = sum(row.analysable for row in rows)

    if len(units) < 5:
        return (
            {
                "status": "insufficient_units",
                "n_units": len(units),
                "n_complaint_reviews": len(complaint_review_ids),
                "minimum_cluster_support": None,
                "distance_threshold": distance_threshold,
                "unit_gate": gate.as_dict(),
                "coverage": _coverage(units, set(), complaint_review_ids),
                "diagnostics": {"pairwise_distance_histogram": [], "cluster_sizes": {}},
                "items": [],
                "other": _other_bucket(units),
            },
            timings,
        )

    embed_started = time.monotonic()
    vectors = embedder.encode([unit.text for unit in units])
    timings["embeddings"] = _elapsed_ms(embed_started)
    if vectors.shape[0] != len(units):
        raise ValueError("Embedding model returned an unexpected number of vectors.")

    diagnostics = theme_diagnostics(vectors)
    cluster_started = time.monotonic()
    labels = _cluster_labels(vectors, distance_threshold)
    timings["clustering"] = _elapsed_ms(cluster_started)

    minimum_support = 2 if len(units) < 15 else 3
    grouped: dict[int, list[int]] = {}
    for index, label in enumerate(labels.tolist()):
        grouped.setdefault(int(label), []).append(index)

    retained = [indexes for indexes in grouped.values() if len(indexes) >= minimum_support]
    retained.sort(
        key=lambda indexes: (-len(indexes), min(units[index].review_id for index in indexes))
    )
    used_indexes = {index for indexes in retained for index in indexes}
    other_units = [unit for index, unit in enumerate(units) if index not in used_indexes]

    themes: list[dict[str, object]] = []
    for theme_number, indexes in enumerate(retained, start=1):
        theme_units = [units[index] for index in indexes]
        theme_vectors = vectors[indexes]
        other_texts = [
            unit.lexical_text for index, unit in enumerate(units) if index not in indexes
        ]
        min_phrase_support = 2 if len(theme_units) >= 2 else 1
        phrases = distinctive_phrases_for_texts(
            [unit.lexical_text for unit in theme_units],
            other_texts,
            min_support=min_phrase_support,
            limit=5,
            app_name=app_name,
        )
        themes.append(
            _theme_record(
                f"theme_{theme_number:02d}",
                theme_units,
                theme_vectors,
                complaint_review_ids,
                n_analysable,
                reference_date,
                phrases,
            )
        )

    return (
        {
            "status": "ok" if themes else "no_supported_clusters",
            "n_units": len(units),
            "n_complaint_reviews": len(complaint_review_ids),
            "minimum_cluster_support": minimum_support,
            "distance_threshold": distance_threshold,
            "unit_gate": gate.as_dict(),
            "coverage": _coverage(units, used_indexes, complaint_review_ids),
            "diagnostics": diagnostics,
            "items": themes,
            "other": _other_bucket(other_units),
        },
        timings,
    )


def complaint_units(
    rows: list[AnalysedReview],
    sentiment: SentimentAnalyzer,
    *,
    positive_review_threshold: float = DEFAULT_UNIT_MIN_NEGATIVE_SCORE_POSITIVE_REVIEWS,
    other_threshold: float = DEFAULT_UNIT_MIN_NEGATIVE_SCORE_OTHER,
) -> UnitGateResult:
    """Classify sentences and keep negative units that pass the configured score gate."""

    candidates: list[tuple[AnalysedReview, int, str]] = []
    for row in rows:
        if not row.analysable or not row.analysis_text.strip():
            continue
        for sentence_index, sentence in enumerate(split_sentences(row.analysis_text)):
            candidates.append((row, sentence_index, sentence))
    if not candidates:
        return UnitGateResult(
            units=[],
            negative_label_candidates=0,
            removed_positive_reviews=0,
            removed_other_reviews=0,
            positive_review_threshold=positive_review_threshold,
            other_threshold=other_threshold,
        )

    predictions = sentiment.predict([sentence for _row, _index, sentence in candidates])
    if len(predictions) != len(candidates):
        raise ValueError("Sentiment model returned an unexpected number of sentence predictions.")

    units: list[ComplaintUnit] = []
    negative_candidates = 0
    removed_positive = 0
    removed_other = 0
    for (row, sentence_index, sentence), prediction in zip(candidates, predictions, strict=True):
        if prediction.label != "negative":
            continue
        negative_candidates += 1
        negative_score = float(prediction.class_scores.get("negative", 0.0))
        threshold = positive_review_threshold if row.rating >= 4 else other_threshold
        if negative_score < threshold:
            if row.rating >= 4:
                removed_positive += 1
            else:
                removed_other += 1
            continue
        units.append(
            ComplaintUnit(
                unit_id=f"{row.source_review_id}:s{sentence_index}",
                review_id=row.source_review_id,
                text=sentence,
                lexical_text=lexical_text(sentence),
                rating=row.rating,
                created_at=row.created_at,
                negative_score=negative_score,
            )
        )
    return UnitGateResult(
        units=units,
        negative_label_candidates=negative_candidates,
        removed_positive_reviews=removed_positive,
        removed_other_reviews=removed_other,
        positive_review_threshold=positive_review_threshold,
        other_threshold=other_threshold,
    )


def split_sentences(value: str) -> list[str]:
    """Split review text conservatively while preserving the sentence text used as evidence."""

    normalised = normalise_text(value)
    if not normalised:
        return []
    return [part.strip() for part in _SENTENCE_SPLIT_RE.split(normalised) if part.strip()]


def theme_diagnostics(vectors: NDArray[np.float64]) -> dict[str, object]:
    """Describe pairwise cosine distances and cluster sizes without changing the default."""

    if vectors.shape[0] < 2:
        return {"pairwise_distance_histogram": [], "cluster_sizes": {}}
    normalised = _normalise_vectors(vectors)
    distances = 1.0 - normalised @ normalised.T
    pairwise = distances[np.triu_indices(normalised.shape[0], k=1)]
    histogram: list[dict[str, object]] = []
    for low, high in zip(
        _DISTANCE_HISTOGRAM_EDGES[:-1],
        _DISTANCE_HISTOGRAM_EDGES[1:],
        strict=True,
    ):
        include_high = high == _DISTANCE_HISTOGRAM_EDGES[-1]
        if include_high:
            count = int(np.sum((pairwise >= low) & (pairwise <= high)))
        else:
            count = int(np.sum((pairwise >= low) & (pairwise < high)))
        histogram.append({"low": low, "high": high, "count": count})

    cluster_sizes: dict[str, object] = {}
    for threshold in DIAGNOSTIC_THRESHOLDS:
        labels = _cluster_labels(normalised, threshold)
        sizes = sorted(Counter(labels.tolist()).values(), reverse=True)
        cluster_sizes[f"{threshold:.2f}"] = {
            "n_clusters": len(sizes),
            "sizes": sizes,
        }
    return {
        "pairwise_distance_histogram": histogram,
        "cluster_sizes": cluster_sizes,
    }


def select_distance_threshold(
    pairs: list[tuple[float, bool]],
    *,
    grid: tuple[float, ...] = THRESHOLD_TUNING_GRID,
    minimum_precision: float = 0.80,
) -> dict[str, object]:
    """Select the largest threshold whose predicted same-issue precision reaches the target."""

    table: list[dict[str, object]] = []
    eligible: list[float] = []
    for threshold in grid:
        predicted_same = [(distance, same) for distance, same in pairs if distance <= threshold]
        true_positive = sum(same for _distance, same in predicted_same)
        predicted_count = len(predicted_same)
        precision = true_positive / predicted_count if predicted_count else None
        qualifies = precision is not None and precision >= minimum_precision
        if qualifies:
            eligible.append(threshold)
        table.append(
            {
                "threshold": threshold,
                "predicted_same": predicted_count,
                "true_same": true_positive,
                "precision": precision,
                "eligible": qualifies,
            }
        )
    return {
        "minimum_precision": minimum_precision,
        "grid": list(grid),
        "selected_threshold": max(eligible) if eligible else None,
        "table": table,
    }


def _theme_record(
    theme_id: str,
    units: list[ComplaintUnit],
    vectors: NDArray[np.float64],
    complaint_review_ids: list[str],
    n_analysable: int,
    reference_date: datetime,
    phrases: list[dict[str, object]],
) -> dict[str, object]:
    review_ids = sorted({unit.review_id for unit in units})
    by_review: dict[str, ComplaintUnit] = {}
    for unit in units:
        by_review.setdefault(unit.review_id, unit)
    representative = _representatives(units, vectors)
    dates = [unit.created_at for unit in units if unit.created_at is not None]
    newest = max(dates, default=None)
    oldest = min(dates, default=None)
    recent_review_ids = {
        unit.review_id
        for unit in units
        if unit.created_at is not None and unit.created_at >= reference_date - timedelta(days=365)
    }
    support = len(review_ids)
    denominator = len(complaint_review_ids)
    interval = wilson_interval(support, denominator) if denominator else None
    stars = [by_review[review_id].rating for review_id in review_ids]
    return {
        "theme_id": theme_id,
        "unit_count": len(units),
        "review_count": support,
        "share_of_complaint_reviews": {
            "value": support / denominator if denominator else None,
            "numerator": support,
            "denominator": denominator,
            "ci95": (
                {"low": interval[0], "high": interval[1], "reason": "conditional_on_clustering"}
                if interval is not None
                else {"low": None, "high": None, "reason": "no_complaint_reviews"}
            ),
        },
        "share_of_analysable": _conditional_share(support, n_analysable),
        "mean_star_rating": sum(stars) / len(stars) if stars else None,
        "top_phrases": phrases,
        "newest_date": newest.isoformat() if newest is not None else None,
        "oldest_date": oldest.isoformat() if oldest is not None else None,
        "share_last_12_months": len(recent_review_ids) / support if support else None,
        "historical": not recent_review_ids,
        "representative_units": representative,
        "evidence_review_ids": review_ids,
    }


def _coverage(
    units: list[ComplaintUnit],
    used_indexes: set[int],
    complaint_review_ids: list[str],
) -> dict[str, object]:
    clustered_review_ids = {units[index].review_id for index in used_indexes}
    units_total = len(units)
    reviews_total = len(complaint_review_ids)
    return {
        "units_clustered": len(used_indexes),
        "units_total": units_total,
        "unit_share": len(used_indexes) / units_total if units_total else None,
        "complaint_reviews_in_themes": len(clustered_review_ids),
        "n_complaint_reviews": reviews_total,
        "review_share": len(clustered_review_ids) / reviews_total if reviews_total else None,
    }


def _conditional_share(successes: int, total: int) -> dict[str, object]:
    interval = wilson_interval(successes, total) if total else None
    return {
        "value": successes / total if total else None,
        "numerator": successes,
        "denominator": total,
        "ci95": (
            {"low": interval[0], "high": interval[1], "reason": "conditional_on_clustering"}
            if interval is not None
            else {"low": None, "high": None, "reason": "no_analysable_reviews"}
        ),
    }


def _representatives(
    units: list[ComplaintUnit], vectors: NDArray[np.float64]
) -> list[dict[str, object]]:
    centroid = np.mean(vectors, axis=0)
    norm = float(np.linalg.norm(centroid))
    if norm:
        centroid = centroid / norm
    scored: list[tuple[bool, float, str, str, ComplaintUnit]] = []
    for unit, vector in zip(units, vectors, strict=True):
        vector_norm = float(np.linalg.norm(vector))
        normalised = vector / vector_norm if vector_norm else vector
        distance = 1.0 - float(np.dot(normalised, centroid))
        # Prefer a complete sentence when one is available; centroid distance remains the
        # secondary ranking criterion, followed by stable IDs for deterministic output.
        scored.append(
            (len(unit.text.strip()) < 40, round(distance, 8), unit.review_id, unit.unit_id, unit)
        )
    scored.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
    return [
        {
            "unit_id": unit.unit_id,
            "review_id": unit.review_id,
            "excerpt": unit.text,
            "distance_to_centroid": distance,
            "negative_score": unit.negative_score,
        }
        for _short, distance, _review_id, _unit_id, unit in scored[:3]
    ]


def _other_bucket(units: list[ComplaintUnit]) -> dict[str, object]:
    return {
        "unit_count": len(units),
        "review_count": len({unit.review_id for unit in units}),
        "evidence_review_ids": sorted({unit.review_id for unit in units}),
    }


def _cluster_labels(vectors: NDArray[np.float64], threshold: float) -> NDArray[np.int64]:
    labels = AgglomerativeClustering(
        n_clusters=None,
        metric="cosine",
        linkage="average",
        distance_threshold=threshold,
    ).fit_predict(vectors)
    return np.asarray(labels, dtype=np.int64)


def _normalise_vectors(vectors: NDArray[np.float64]) -> NDArray[np.float64]:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    safe_norms = np.where(norms == 0.0, 1.0, norms)
    return vectors / safe_norms


def _validate_unit_threshold(value: float) -> None:
    if not 0.0 <= value <= 1.0:
        raise ValueError("Complaint-unit negative-score thresholds must be between 0 and 1.")


def _validate_distance_threshold(value: float) -> None:
    if not 0.0 < value <= 2.0:
        raise ValueError("Theme distance threshold must satisfy 0 < threshold <= 2.")


def _hits(text: str, terms: tuple[str, ...]) -> float:
    return float(sum(text.count(term) for term in terms))


def _configure_offline_environment() -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["DISABLE_SAFETENSORS_CONVERSION"] = "1"
    if os.name == "nt":
        os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"


def _elapsed_ms(started: float) -> float:
    return round((time.monotonic() - started) * 1000.0, 3)
