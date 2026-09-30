"""Deterministic helpers for the hand-labelled evaluation workflow."""

from __future__ import annotations

import csv
import json
import math
import os
import random
import time
import warnings
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

import numpy as np
from huggingface_hub import snapshot_download
from numpy.typing import NDArray
from scipy.stats import bootstrap
from sklearn.metrics import (
    accuracy_score,
    cohen_kappa_score,
    confusion_matrix,
    f1_score,
    precision_recall_fscore_support,
)

from appstore_review_analysis.analysis.metrics import wilson_interval
from appstore_review_analysis.analysis.sentiment import (
    SentimentAnalyzer,
    SentimentResult,
)
from appstore_review_analysis.analysis.themes import select_distance_threshold
from appstore_review_analysis.collection.sampling import sample_ranks
from appstore_review_analysis.domain import Review
from appstore_review_analysis.hf_download import suppress_public_download_auth_warning
from appstore_review_analysis.text import analysis_text, preprocess_review

GoldLabel = Literal["positive", "negative", "neutral", "mixed"]
ThreeClassLabel = Literal["negative", "neutral", "positive"]
BinaryUnitLabel = Literal["complaint", "not_complaint"]
PairLabel = Literal["same_issue", "different_issue"]

GOLD_SEED = 20260928
ORDER_SEED = 20260928
RELABEL_SEED = 20260928
PAIR_SEED = 20260928
STRATUM_COUNTS = {1: 40, 2: 25, 3: 25, 4: 25, 5: 35}
THREE_CLASS_LABELS: tuple[ThreeClassLabel, ...] = ("negative", "neutral", "positive")
GOLD_LABELS: tuple[GoldLabel, ...] = ("positive", "negative", "neutral", "mixed")
UNIT_LABELS: tuple[BinaryUnitLabel, ...] = ("complaint", "not_complaint")
PAIR_LABELS: tuple[PairLabel, ...] = ("same_issue", "different_issue")

TABULARIS_MODEL_ID = "tabularisai/robust-sentiment-analysis"
TABULARIS_MODEL_REVISION = "c542a281e22b3d840a0b3f6c129acf8e357aed50"
TABULARIS_MODEL_DIRNAME = "tabularisai--robust-sentiment-analysis"
SIEBERT_MODEL_ID = "siebert/sentiment-roberta-large-english"
SIEBERT_MODEL_REVISION = "74cea614e245b0832c770ec9aa51bd58df965b9c"
SIEBERT_MODEL_DIRNAME = "siebert--sentiment-roberta-large-english"
_EVAL_MODEL_ALLOW_PATTERNS = [
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "vocab.json",
    "vocab.txt",
    "merges.txt",
    "model.safetensors",
    "pytorch_model.bin",
]
_REVISION_MARKER = ".pinned_revision"


@dataclass(frozen=True)
class GoldItem:
    review_id: str
    rank: int
    stratum: int
    star: int
    text: str


def review_text(review: Review) -> str:
    """Return the privacy-minimised title/body text used for human labelling."""

    text, _duplicate = analysis_text(review.title, review.body)
    return text


def read_population(path: Path) -> list[Review]:
    """Read a population JSONL produced by ``reviews walk``."""

    rows: list[Review] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                rows.append(Review.model_validate_json(line))
            except Exception as exc:
                raise ValueError(f"invalid population row {line_number}") from exc
    return rows


def build_gold_items(
    population: Sequence[Review],
    *,
    excluded_review_ids: set[str],
    language_filter: Callable[[Review], bool] | None = None,
) -> list[GoldItem]:
    """Draw the fixed 150-item stratified gold candidate set."""

    is_eligible = language_filter or _default_language_filter
    candidates = [
        row
        for row in population
        if row.source_review_id not in excluded_review_ids and is_eligible(row)
    ]
    selected_by_star: dict[int, list[Review]] = {}
    for star, required in STRATUM_COUNTS.items():
        stratum = sorted(
            (row for row in candidates if row.rating == star),
            key=lambda row: (_rank_or_last(row.rank), row.source_review_id),
        )
        if len(stratum) < required:
            raise ValueError(
                f"star {star} has {len(stratum)} eligible reviews; {required} are required"
            )
        indexes = sample_ranks(len(stratum), required, random.Random(GOLD_SEED))
        selected_by_star[star] = [stratum[index] for index in indexes]

    items: list[GoldItem] = []
    for star in range(1, 6):
        for row in selected_by_star[star]:
            items.append(
                GoldItem(
                    review_id=row.source_review_id,
                    rank=_rank_or_last(row.rank),
                    stratum=star,
                    star=row.rating,
                    text=review_text(row),
                )
            )
    return items


def frame_weights(population: Sequence[Review], *, source_path: Path) -> dict[str, object]:
    """Return written-review star shares for prevalence reweighting."""

    counts = Counter(row.rating for row in population)
    total = len(population)
    mtime = datetime.fromtimestamp(source_path.stat().st_mtime, tz=UTC).isoformat()
    return {
        "total": total,
        "star_counts": {str(star): counts.get(star, 0) for star in range(1, 6)},
        "star_shares": {
            str(star): (counts.get(star, 0) / total if total else None) for star in range(1, 6)
        },
        "collected_at": mtime,
        "collected_at_source": "population_jsonl_file_mtime_utc",
    }


def write_gold_items(path: Path, items: Sequence[GoldItem]) -> None:
    """Write only the allowlisted gold-item fields."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["review_id", "rank", "stratum", "star", "text"],
            lineterminator="\n",
        )
        writer.writeheader()
        for item in items:
            writer.writerow(
                {
                    "review_id": item.review_id,
                    "rank": item.rank,
                    "stratum": item.stratum,
                    "star": item.star,
                    "text": item.text,
                }
            )


def read_gold_items(path: Path) -> list[GoldItem]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        expected = {"review_id", "rank", "stratum", "star", "text"}
        if reader.fieldnames is None or set(reader.fieldnames) != expected:
            raise ValueError("gold_items.csv has an unexpected schema")
        result: list[GoldItem] = []
        for row_number, row in enumerate(reader, start=2):
            result.append(
                GoldItem(
                    review_id=_required(row.get("review_id"), row_number, "review_id"),
                    rank=int(_required(row.get("rank"), row_number, "rank")),
                    stratum=int(_required(row.get("stratum"), row_number, "stratum")),
                    star=int(_required(row.get("star"), row_number, "star")),
                    text=_required(row.get("text"), row_number, "text"),
                )
            )
    if len({item.review_id for item in result}) != len(result):
        raise ValueError("gold_items.csv contains duplicate review_id values")
    return result


def shuffled_gold_items(items: Sequence[GoldItem]) -> list[GoldItem]:
    """Return one seeded random order for all labelling sheets."""

    ordered = sorted(items, key=lambda item: item.review_id)
    rng = random.Random(ORDER_SEED)
    decorated = [(rng.random(), item.review_id, item) for item in ordered]
    decorated.sort(key=lambda row: (row[0], row[1]))
    return [item for _key, _id, item in decorated]


def label_sheet_rows(items: Sequence[GoldItem]) -> list[dict[str, str]]:
    """Build the seeded labelling sheet: text only, labels left empty for the annotator."""

    return [
        {"review_id": item.review_id, "text": item.text, "human_label": ""}
        for item in shuffled_gold_items(items)
    ]


def classification_metrics(y_true: Sequence[str], y_pred: Sequence[str]) -> dict[str, object]:
    """Return fixed-label 3-class metrics with stable zero-division behaviour."""

    if len(y_true) != len(y_pred) or not y_true:
        raise ValueError("classification inputs must be non-empty and equal length")
    unsupported = (set(y_true) | set(y_pred)) - set(THREE_CLASS_LABELS)
    if unsupported:
        raise ValueError(f"unsupported 3-class labels: {sorted(unsupported)}")
    precision, recall, f1, support = precision_recall_fscore_support(
        y_true,
        y_pred,
        labels=list(THREE_CLASS_LABELS),
        zero_division=0,
    )
    per_class = {
        label: {
            "precision": float(precision[index]),
            "recall": float(recall[index]),
            "f1": float(f1[index]),
            "support": int(support[index]),
        }
        for index, label in enumerate(THREE_CLASS_LABELS)
    }
    return {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "macro_f1": float(
            f1_score(
                y_true,
                y_pred,
                labels=list(THREE_CLASS_LABELS),
                average="macro",
                zero_division=0,
            )
        ),
        "negative_f1": float(per_class["negative"]["f1"]),
        "per_class": per_class,
        "confusion_matrix": confusion_matrix(
            y_true, y_pred, labels=list(THREE_CLASS_LABELS)
        ).tolist(),
    }


def metric_bca_ci(
    y_true: Sequence[str],
    y_pred: Sequence[str],
    *,
    metric: Literal["accuracy", "macro_f1", "negative_f1"],
    seed: int,
    stage_id: int,
) -> dict[str, float | str | None]:
    """Seeded paired BCa interval for one classifier metric."""

    if len(y_true) < 2:
        return {"low": None, "high": None, "reason": "insufficient_items"}
    mapping: dict[str, int] = {label: index for index, label in enumerate(THREE_CLASS_LABELS)}
    true = np.array([mapping[label] for label in y_true], dtype=np.int64)
    pred = np.array([mapping[label] for label in y_pred], dtype=np.int64)

    def statistic(true_values: NDArray[np.int_], pred_values: NDArray[np.int_]) -> float:
        return _encoded_metric(true_values.astype(int), pred_values.astype(int), metric)

    try:
        result = bootstrap(
            (true, pred),
            statistic,
            paired=True,
            method="BCa",
            n_resamples=9999,
            rng=np.random.default_rng([seed, stage_id]),
        )
        low = float(result.confidence_interval.low)
        high = float(result.confidence_interval.high)
    except (ValueError, FloatingPointError):
        return {"low": None, "high": None, "reason": "degenerate"}
    if not (math.isfinite(low) and math.isfinite(high)):
        return {"low": None, "high": None, "reason": "degenerate"}
    return {"low": low, "high": high, "reason": None}


def paired_negative_f1_difference_ci(
    y_true: Sequence[str],
    shipping_pred: Sequence[str],
    challenger_pred: Sequence[str],
    *,
    seed: int = GOLD_SEED,
) -> dict[str, float | str | None]:
    """BCa CI for challenger minus shipping negative-class F1."""

    if len(y_true) < 2:
        return {"low": None, "high": None, "reason": "insufficient_items"}
    mapping: dict[str, int] = {label: index for index, label in enumerate(THREE_CLASS_LABELS)}
    true = np.array([mapping[label] for label in y_true], dtype=np.int64)
    shipping = np.array([mapping[label] for label in shipping_pred], dtype=np.int64)
    challenger = np.array([mapping[label] for label in challenger_pred], dtype=np.int64)

    def statistic(
        true_values: NDArray[np.int_],
        shipping_values: NDArray[np.int_],
        challenger_values: NDArray[np.int_],
    ) -> float:
        challenger_score = _encoded_metric(
            true_values.astype(int), challenger_values.astype(int), "negative_f1"
        )
        shipping_score = _encoded_metric(
            true_values.astype(int), shipping_values.astype(int), "negative_f1"
        )
        return challenger_score - shipping_score

    try:
        result = bootstrap(
            (true, shipping, challenger),
            statistic,
            paired=True,
            method="BCa",
            n_resamples=9999,
            rng=np.random.default_rng([seed, 14]),
        )
        low = float(result.confidence_interval.low)
        high = float(result.confidence_interval.high)
    except (ValueError, FloatingPointError):
        return {"low": None, "high": None, "reason": "degenerate"}
    if not (math.isfinite(low) and math.isfinite(high)):
        return {"low": None, "high": None, "reason": "degenerate"}
    return {"low": low, "high": high, "reason": None}


def prevalence_reweighted_accuracy(
    *,
    stars: Sequence[int],
    y_true: Sequence[str],
    y_pred: Sequence[str],
    frame_star_shares: dict[str, float | None],
) -> float:
    """Reweight stratum-specific accuracy to the walked written-review frame."""

    if not (len(stars) == len(y_true) == len(y_pred)):
        raise ValueError("prevalence inputs must have equal length")
    total = 0.0
    weight_total = 0.0
    for star in range(1, 6):
        indexes = [index for index, value in enumerate(stars) if value == star]
        weight_raw = frame_star_shares.get(str(star))
        if weight_raw is None:
            continue
        if not indexes:
            raise ValueError(f"no evaluable gold items for star {star}")
        accuracy = sum(y_true[index] == y_pred[index] for index in indexes) / len(indexes)
        weight = float(weight_raw)
        total += accuracy * weight
        weight_total += weight
    if weight_total <= 0:
        raise ValueError("frame weights contain no usable star shares")
    return total / weight_total


def wilson_summary(successes: int, n: int) -> dict[str, float | int | None]:
    interval = wilson_interval(successes, n)
    return {
        "successes": successes,
        "n": n,
        "value": successes / n if n else None,
        "low": interval[0] if interval else None,
        "high": interval[1] if interval else None,
    }


def annotator_relabel_statistics(
    original_labels: dict[str, str],
    relabels: dict[str, str],
    *,
    seed: int = GOLD_SEED,
) -> dict[str, object]:
    ids = sorted(relabels)
    if not ids:
        return {"status": "not_run", "reason": "no_relabels"}
    original = [original_labels[item_id] for item_id in ids]
    repeated = [relabels[item_id] for item_id in ids]

    if len(ids) < 2:
        return {
            "status": "run",
            "n": len(ids),
            "kappa": None,
            "bootstrap_ci95": {
                "low": None,
                "high": None,
                "reason": "insufficient_items",
            },
        }

    if original == repeated and len(set(original)) == 1:
        return {
            "status": "run",
            "n": len(ids),
            "kappa": None,
            "bootstrap_ci95": {
                "low": None,
                "high": None,
                "reason": "undefined_single_class",
            },
        }

    value = float(cohen_kappa_score(original, repeated))
    if original == repeated:
        return {
            "status": "run",
            "n": len(ids),
            "kappa": value,
            "bootstrap_ci95": {
                "low": value,
                "high": value,
                "reason": "degenerate_perfect_agreement",
            },
        }

    encoded_labels: dict[str, int] = {label: index for index, label in enumerate(GOLD_LABELS)}
    a = np.array([encoded_labels[label] for label in original], dtype=np.int64)
    b = np.array([encoded_labels[label] for label in repeated], dtype=np.int64)

    def statistic(left: NDArray[np.int_], right: NDArray[np.int_]) -> float:
        return float(cohen_kappa_score(left.astype(int), right.astype(int)))

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            result = bootstrap(
                (a, b),
                statistic,
                paired=True,
                method="BCa",
                n_resamples=9999,
                rng=np.random.default_rng([seed, 15]),
            )
        low = float(result.confidence_interval.low)
        high = float(result.confidence_interval.high)
        ci: dict[str, float | str | None] = (
            {"low": low, "high": high, "reason": None}
            if math.isfinite(low) and math.isfinite(high)
            else {"low": None, "high": None, "reason": "degenerate"}
        )
    except (ValueError, FloatingPointError, RuntimeWarning):
        ci = {"low": None, "high": None, "reason": "degenerate"}
    return {"status": "run", "n": len(ids), "kappa": value, "bootstrap_ci95": ci}


def star_baseline(stars: Sequence[int]) -> list[ThreeClassLabel]:
    return ["negative" if star <= 2 else "neutral" if star == 3 else "positive" for star in stars]


def timed_predictions(
    analyzer: SentimentAnalyzer, texts: list[str]
) -> tuple[list[ThreeClassLabel], float]:
    started = time.perf_counter()
    results = analyzer.predict(texts)
    elapsed = time.perf_counter() - started
    predictions: list[ThreeClassLabel] = [result.label for result in results]
    latency_per_100_ms = (elapsed / len(texts) * 100.0 * 1000.0) if texts else 0.0
    return predictions, latency_per_100_ms


def download_evaluation_model(
    models_dir: Path, *, model_id: str, revision: str, dirname: str
) -> Path:
    """Download a comparator at an exact immutable revision and record the pin."""

    target = models_dir / dirname
    target.mkdir(parents=True, exist_ok=True)
    with suppress_public_download_auth_warning():
        snapshot_download(
            repo_id=model_id,
            revision=revision,
            local_dir=target,
            allow_patterns=_EVAL_MODEL_ALLOW_PATTERNS,
        )
    (target / _REVISION_MARKER).write_text(revision + "\n", encoding="utf-8", newline="\n")
    return target


def assert_pinned_model(path: Path, expected_revision: str) -> None:
    marker = path / _REVISION_MARKER
    if not marker.exists() or marker.read_text(encoding="utf-8").strip() != expected_revision:
        raise ValueError(
            f"evaluation model at {path} is not verified at revision {expected_revision}"
        )


class FiveClassSentimentAdapter:
    """Map a pinned 5-class sentiment classifier into negative/neutral/positive."""

    model_id = TABULARIS_MODEL_ID
    model_revision = TABULARIS_MODEL_REVISION

    def __init__(self, tokenizer: Any, model: Any, *, batch_size: int = 32) -> None:
        self.tokenizer = tokenizer
        self.model = model
        self.batch_size = batch_size
        self.model.eval()
        self.id2label = {int(key): str(value) for key, value in dict(model.config.id2label).items()}

    @classmethod
    def load(cls, models_dir: Path) -> FiveClassSentimentAdapter:
        path = models_dir / TABULARIS_MODEL_DIRNAME
        assert_pinned_model(path, TABULARIS_MODEL_REVISION)
        _offline_hf()
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True)
        model = AutoModelForSequenceClassification.from_pretrained(path, local_files_only=True)
        return cls(tokenizer, model)

    def predict(self, texts: list[str]) -> list[SentimentResult]:
        import torch

        output: list[SentimentResult] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            encoded = self.tokenizer(
                batch,
                padding=True,
                truncation=True,
                max_length=512,
                return_tensors="pt",
            )
            with torch.inference_mode():
                probabilities = torch.softmax(self.model(**encoded).logits, dim=-1).cpu().numpy()
            for row in probabilities:
                scores = {"negative": 0.0, "neutral": 0.0, "positive": 0.0}
                for index, probability in enumerate(row.tolist()):
                    bucket = _five_class_bucket(self.id2label.get(index, str(index)), index)
                    scores[bucket] += float(probability)
                label = cast(ThreeClassLabel, max(scores.items(), key=lambda item: item[1])[0])
                output.append(
                    SentimentResult(
                        label=label,
                        class_scores=scores,
                        truncated=False,
                        model_id=self.model_id,
                        model_revision=self.model_revision,
                    )
                )
        return output


class BinarySentimentAdapter:
    """Reference-only pinned binary sentiment model."""

    model_id = SIEBERT_MODEL_ID
    model_revision = SIEBERT_MODEL_REVISION

    def __init__(self, tokenizer: Any, model: Any, *, batch_size: int = 32) -> None:
        self.tokenizer = tokenizer
        self.model = model
        self.batch_size = batch_size
        self.model.eval()
        self.id2label = {
            int(key): str(value).lower() for key, value in dict(model.config.id2label).items()
        }

    @classmethod
    def load(cls, models_dir: Path) -> BinarySentimentAdapter:
        path = models_dir / SIEBERT_MODEL_DIRNAME
        assert_pinned_model(path, SIEBERT_MODEL_REVISION)
        _offline_hf()
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True)
        model = AutoModelForSequenceClassification.from_pretrained(path, local_files_only=True)
        return cls(tokenizer, model)

    def predict_labels(self, texts: list[str]) -> list[Literal["negative", "positive"]]:
        import torch

        labels: list[Literal["negative", "positive"]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            encoded = self.tokenizer(
                batch,
                padding=True,
                truncation=True,
                max_length=512,
                return_tensors="pt",
            )
            with torch.inference_mode():
                predicted = torch.argmax(self.model(**encoded).logits, dim=-1).cpu().tolist()
            for index in predicted:
                raw = self.id2label[int(index)]
                labels.append("negative" if "neg" in raw else "positive")
        return labels


def unit_metrics(rows: Sequence[dict[str, str]]) -> dict[str, object]:
    """Precision/recall with Wilson intervals for model complaint-unit decisions."""

    predicted = [row for row in rows if row["model_label"] == "complaint"]
    actual = [row for row in rows if row["human_label"] == "complaint"]
    true_positive = sum(
        row["model_label"] == "complaint" and row["human_label"] == "complaint" for row in rows
    )
    missed = [
        row
        for row in rows
        if row["human_label"] == "complaint" and row["model_label"] != "complaint"
    ]
    return {
        "status": "run",
        "n": len(rows),
        "precision": wilson_summary(true_positive, len(predicted)),
        "recall": wilson_summary(true_positive, len(actual)),
        "missed_complaints": [{"id": row["id"], "text": row["text"][:240]} for row in missed[:5]],
    }


def pair_threshold_metrics(rows: Sequence[dict[str, str]]) -> dict[str, object]:
    pairs = [(float(row["distance"]), row["human_label"] == "same_issue") for row in rows]
    result = select_distance_threshold(pairs)
    return {"status": "run", **result}


def render_results_markdown(results: dict[str, Any]) -> str:
    """Render the complete evaluation evidence without inventing missing stages."""

    lines = ["# Evaluation results", ""]
    benchmark = cast(dict[str, Any], results.get("benchmark", {}))
    lines.extend(["## Sentiment benchmark", ""])
    if benchmark.get("status") != "run":
        lines.extend(["Evaluation not run.", ""])
    else:
        lines.extend(
            [
                "Primary metric: **negative-class F1**. Mixed items are excluded from "
                "3-class metrics.",
                "",
                (
                    f"Evaluable n: **{benchmark.get('evaluable_n')}**; mixed excluded: "
                    f"**{benchmark.get('mixed_n')}**; neutral support: "
                    f"**{benchmark.get('neutral_support')}**."
                ),
                "",
                str(benchmark.get("neutral_note") or ""),
                "",
                "Decision rule fixed before running: "
                + str(benchmark.get("decision_rule") or "not recorded"),
                "",
                (
                    "| Model | Revision | Size MiB | Accuracy (95% CI) | Macro-F1 (95% CI) | "
                    "Negative F1 (95% CI) | Reweighted accuracy | CPU ms / 100 |"
                ),
                "| --- | --- | ---: | --- | --- | --- | ---: | ---: |",
            ]
        )
        for model in cast(list[dict[str, Any]], benchmark.get("models", [])):
            metrics = cast(dict[str, Any], model.get("metrics", {}))
            ci95 = cast(dict[str, Any], metrics.get("ci95", {}))
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(model.get("name") or "unknown"),
                        str(model.get("revision") or "n/a"),
                        _md_number(float(model.get("size_bytes", 0)) / (1024 * 1024), 1),
                        _md_metric_ci(metrics.get("accuracy"), ci95.get("accuracy")),
                        _md_metric_ci(metrics.get("macro_f1"), ci95.get("macro_f1")),
                        _md_metric_ci(metrics.get("negative_f1"), ci95.get("negative_f1")),
                        _md_number(metrics.get("prevalence_reweighted_accuracy"), 3),
                        _md_number(model.get("latency_per_100_ms"), 1),
                    ]
                )
                + " |"
            )
        lines.append("")

        paired = cast(
            dict[str, Any],
            benchmark.get("paired_tabularis_minus_shipping_negative_f1_ci95", {}),
        )
        lines.extend(
            [
                "Tabularis minus shipping negative-F1 paired BCa 95% CI: "
                + _md_ci(paired, 3)
                + ".",
                "",
                "Decision-rule switch condition met: "
                f"**{bool(benchmark.get('switch_supported'))}**.",
                "",
            ]
        )

        for model in cast(list[dict[str, Any]], benchmark.get("models", [])):
            metrics = cast(dict[str, Any], model.get("metrics", {}))
            per_class = cast(dict[str, Any], metrics.get("per_class", {}))
            lines.extend(
                [
                    f"### {model.get('name', 'unknown')} class metrics",
                    "",
                    "| Class | Precision | Recall | F1 | Support |",
                    "| --- | ---: | ---: | ---: | ---: |",
                ]
            )
            for label in THREE_CLASS_LABELS:
                row = cast(dict[str, Any], per_class.get(label, {}))
                lines.append(
                    f"| {label} | {_md_number(row.get('precision'), 3)} | "
                    f"{_md_number(row.get('recall'), 3)} | {_md_number(row.get('f1'), 3)} | "
                    f"{row.get('support', 0)} |"
                )
            lines.extend(
                [
                    "",
                    "Confusion matrix (rows=true, columns=predicted; negative/neutral/positive):",
                    "",
                    "```json",
                    json.dumps(metrics.get("confusion_matrix", []), ensure_ascii=False),
                    "```",
                    "",
                ]
            )

        references = cast(list[dict[str, Any]], benchmark.get("references", []))
        if references:
            lines.extend(
                [
                    "### Reference-only models",
                    "",
                    "| Model | Revision | Scope | n | Accuracy | CPU ms / 100 |",
                    "| --- | --- | --- | ---: | ---: | ---: |",
                ]
            )
            for reference in references:
                lines.append(
                    f"| {reference.get('name')} | {reference.get('revision')} | "
                    f"{reference.get('scope')} | {reference.get('n')} | "
                    f"{_md_number(reference.get('accuracy'), 3)} | "
                    f"{_md_number(reference.get('latency_per_100_ms'), 1)} |"
                )
            lines.append("")

        errors = cast(list[dict[str, Any]], benchmark.get("error_analysis", []))
        lines.extend(["### Error analysis", ""])
        if errors:
            lines.extend(
                [
                    "| Review ID | Star | Gold | Shipping | Tabularis | Star floor | "
                    "Text excerpt |",
                    "| --- | ---: | --- | --- | --- | --- | --- |",
                ]
            )
            for row in errors:
                excerpt = str(row.get("text") or "").replace("|", "\\|").replace("\n", " ")
                lines.append(
                    f"| {row.get('review_id')} | {row.get('star')} | {row.get('gold')} | "
                    f"{row.get('shipping')} | {row.get('tabularis')} | "
                    f"{row.get('star_baseline')} | {excerpt} |"
                )
            lines.append("")
        else:
            lines.extend(["No model errors were available for the deterministic error table.", ""])

    relabel = cast(dict[str, Any], results.get("annotator_relabel", {}))
    lines.extend(["## Annotator repeatability", ""])
    if relabel.get("status") != "run":
        lines.extend([f"Evaluation not run: {relabel.get('reason', 'missing inputs')}.", ""])
    else:
        relabel_ci = cast(dict[str, Any], relabel.get("bootstrap_ci95", {}))
        lines.extend(
            [
                f"Repeated items: **{relabel.get('n')}**.",
                "",
                "Cohen's kappa: " + _md_number(relabel.get("kappa"), 3) + ".",
                "",
                "Seeded bootstrap 95% CI: " + _md_ci(relabel_ci, 3) + ".",
                "",
            ]
        )
        if relabel_ci.get("reason") == "degenerate_perfect_agreement":
            lines.extend(
                [
                    "All repeated labels matched the originals, so the empirical "
                    "bootstrap distribution is degenerate and the interval collapses "
                    "to the observed kappa.",
                    "",
                ]
            )
        lines.extend(
            [
                "Session separation is not encoded in labels_relabel.csv; interpret this as "
                "repeat-label consistency unless later-session collection timing is documented.",
                "",
            ]
        )

    units = cast(dict[str, Any], results.get("complaint_units", {}))
    lines.extend(["## Complaint-unit check", ""])
    if units.get("status") != "run":
        lines.extend([f"Evaluation not run: {units.get('reason', 'missing inputs')}.", ""])
    else:
        lines.extend(
            [
                f"Evaluated sentences: **{units.get('n')}**.",
                "",
                "Precision: " + _md_wilson(cast(dict[str, Any], units.get("precision", {}))) + ".",
                "",
                "Recall: " + _md_wilson(cast(dict[str, Any], units.get("recall", {}))) + ".",
                "",
            ]
        )
        subgroups = cast(dict[str, Any], units.get("subgroups", {}))
        if subgroups:
            lines.extend(
                [
                    "| Subgroup | Complaint recall (95% Wilson CI) |",
                    "| --- | --- |",
                    "| Mixed reviews | "
                    f"{_md_wilson(cast(dict[str, Any], subgroups.get('mixed_reviews', {})))} |",
                    "| 4-5 star reviews | "
                    f"{_md_wilson(cast(dict[str, Any], subgroups.get('rating_4_5', {})))} |",
                    "",
                ]
            )
        missed = cast(list[dict[str, Any]], units.get("missed_complaints", []))
        if missed:
            lines.extend(["Five missed complaints (or all, when fewer than five):", ""])
            for row in missed[:5]:
                text = str(row.get("text") or "").replace("\n", " ")
                lines.append(f"- `{row.get('id')}` — {text}")
            lines.append("")

    threshold = cast(dict[str, Any], results.get("theme_threshold", {}))
    lines.extend(["## Theme threshold", ""])
    if threshold.get("status") != "run":
        lines.extend([f"Evaluation not run: {threshold.get('reason', 'missing inputs')}.", ""])
    else:
        selected = threshold.get("selected_threshold")
        lines.extend(
            [
                "Selection rule: largest grid threshold with same-issue precision >= "
                + _md_number(threshold.get("minimum_precision"), 2)
                + ".",
                "",
                "Selected threshold: "
                + (
                    f"**{float(selected):.2f}**"
                    if isinstance(selected, (int, float))
                    else "**none**"
                )
                + ".",
                "",
                "| Threshold | Predicted same | True same | Precision | Eligible |",
                "| ---: | ---: | ---: | ---: | --- |",
            ]
        )
        for row in cast(list[dict[str, Any]], threshold.get("table", [])):
            lines.append(
                f"| {_md_number(row.get('threshold'), 2)} | {row.get('predicted_same')} | "
                f"{row.get('true_same')} | {_md_number(row.get('precision'), 3)} | "
                f"{bool(row.get('eligible'))} |"
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _md_number(value: object, decimals: int) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "n/a"
    number = float(value)
    if not math.isfinite(number):
        return "n/a"
    return f"{number:.{decimals}f}"


def _md_ci(value: dict[str, Any], decimals: int) -> str:
    low = _md_number(value.get("low"), decimals)
    high = _md_number(value.get("high"), decimals)
    if "n/a" in {low, high}:
        reason = value.get("reason")
        return f"n/a ({reason})" if reason else "n/a"
    return f"[{low}, {high}]"


def _md_metric_ci(value: object, ci: object) -> str:
    point = _md_number(value, 3)
    interval = _md_ci(cast(dict[str, Any], ci) if isinstance(ci, dict) else {}, 3)
    return f"{point} {interval}"


def _md_wilson(value: dict[str, Any]) -> str:
    point = _md_number(value.get("value"), 3)
    low = _md_number(value.get("low"), 3)
    high = _md_number(value.get("high"), 3)
    n = value.get("n")
    if "n/a" in {point, low, high}:
        return f"n/a (n={n})"
    return f"{point} [{low}, {high}], n={n}"


def _default_language_filter(review: Review) -> bool:
    return preprocess_review(review).analysable


def _rank_or_last(rank: int | None) -> int:
    return rank if rank is not None else 2**63 - 1


def _required(value: str | None, row_number: int, field: str) -> str:
    if value is None or not value.strip():
        raise ValueError(f"missing {field} on row {row_number}")
    return value.strip()


def _encoded_metric(true: NDArray[np.int_], pred: NDArray[np.int_], metric: str) -> float:
    labels = [0, 1, 2]
    if metric == "accuracy":
        return float(accuracy_score(true, pred))
    if metric == "macro_f1":
        return float(f1_score(true, pred, labels=labels, average="macro", zero_division=0))
    return float(f1_score(true, pred, labels=[0], average="macro", zero_division=0))


def _five_class_bucket(label: str, index: int) -> ThreeClassLabel:
    normalised = label.strip().lower().replace("_", " ")
    if "neutral" in normalised or normalised in {"3", "3 star", "3 stars"}:
        return "neutral"
    if "negative" in normalised or normalised.startswith(("1", "2")):
        return "negative"
    if "positive" in normalised or normalised.startswith(("4", "5")):
        return "positive"
    if index <= 1:
        return "negative"
    if index == 2:
        return "neutral"
    return "positive"


def _offline_hf() -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["DISABLE_SAFETENSORS_CONVERSION"] = "1"
