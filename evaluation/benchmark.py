from __future__ import annotations

import argparse
import csv
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from appstore_review_analysis.analysis.sentiment import (
    SENTIMENT_MODEL_ID,
    SENTIMENT_MODEL_REVISION,
    TransformerSentiment,
    sentiment_model_path,
)
from appstore_review_analysis.config import Settings
from appstore_review_analysis.evaluation import (
    GOLD_SEED,
    SIEBERT_MODEL_DIRNAME,
    SIEBERT_MODEL_ID,
    SIEBERT_MODEL_REVISION,
    TABULARIS_MODEL_DIRNAME,
    TABULARIS_MODEL_ID,
    TABULARIS_MODEL_REVISION,
    BinarySentimentAdapter,
    FiveClassSentimentAdapter,
    annotator_relabel_statistics,
    category_audit_metrics,
    classification_metrics,
    metric_bca_ci,
    pair_threshold_metrics,
    paired_negative_f1_difference_ci,
    prevalence_reweighted_accuracy,
    read_gold_items,
    render_results_markdown,
    star_baseline,
    timed_predictions,
    unit_metrics,
)


def _read_final_labels(path: Path) -> dict[str, str]:
    labels: dict[str, str] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            item_id = str(row["review_id"]).strip()
            label = str(row["human_label"]).strip().lower()
            if item_id in labels:
                raise ValueError(f"duplicate final label: {item_id}")
            labels[item_id] = label
    return labels


def _read_simple_labels(path: Path) -> dict[str, str]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return {
            str(row["review_id"]).strip(): str(row["human_label"]).strip().lower()
            for row in csv.DictReader(handle)
        }


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _model_record(
    *,
    name: str,
    model_id: str,
    revision: str,
    size_bytes: int,
    y_true: list[str],
    y_pred: list[str],
    stars: list[int],
    frame_shares: dict[str, float | None],
    latency_per_100_ms: float,
    eligible: bool,
    license_name: str | None,
) -> dict[str, Any]:
    metrics = classification_metrics(y_true, y_pred)
    metrics["ci95"] = {
        "accuracy": metric_bca_ci(y_true, y_pred, metric="accuracy", seed=GOLD_SEED, stage_id=11),
        "macro_f1": metric_bca_ci(y_true, y_pred, metric="macro_f1", seed=GOLD_SEED, stage_id=12),
        "negative_f1": metric_bca_ci(
            y_true, y_pred, metric="negative_f1", seed=GOLD_SEED, stage_id=13
        ),
    }
    metrics["prevalence_reweighted_accuracy"] = prevalence_reweighted_accuracy(
        stars=stars,
        y_true=y_true,
        y_pred=y_pred,
        frame_star_shares=frame_shares,
    )
    return {
        "name": name,
        "model_id": model_id,
        "revision": revision,
        "size_bytes": size_bytes,
        "eligible_challenger": eligible,
        "license": license_name,
        "metrics": metrics,
        "latency_per_100_ms": latency_per_100_ms,
    }


def _unit_section(path: Path, shipping: TransformerSentiment, settings: Settings) -> dict[str, Any]:
    if not path.exists():
        return {"status": "not_run", "reason": "units_gold.csv missing"}
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    predictions = shipping.predict([row["text"] for row in rows])
    evaluated: list[dict[str, str]] = []
    for row, prediction in zip(rows, predictions, strict=True):
        star = int(row["star"])
        threshold = (
            settings.unit_min_negative_score_positive_reviews
            if star >= 4
            else settings.unit_min_negative_score_other
        )
        negative_score = float(prediction.class_scores.get("negative", 0.0))
        model_label = (
            "complaint"
            if prediction.label == "negative" and negative_score >= threshold
            else "not_complaint"
        )
        evaluated.append({**row, "model_label": model_label})
    result = unit_metrics(evaluated)
    result["subgroups"] = {
        "mixed_reviews": _recall_subgroup(
            evaluated, lambda row: row["review_gold_label"] == "mixed"
        ),
        "rating_4_5": _recall_subgroup(evaluated, lambda row: int(row["star"]) >= 4),
    }
    return result


def _recall_subgroup(
    rows: list[dict[str, str]], predicate: Callable[[dict[str, str]], bool]
) -> dict[str, Any]:
    subset = [row for row in rows if predicate(row)]
    actual = [row for row in subset if row["human_label"] == "complaint"]
    true_positive = sum(row["model_label"] == "complaint" for row in actual)
    from appstore_review_analysis.evaluation import wilson_summary

    return wilson_summary(true_positive, len(actual))


def _pair_section(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"status": "not_run", "reason": "pairs_gold.csv missing"}
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 45:
        raise ValueError("pairs_gold.csv must contain exactly 45 pairs")
    return pair_threshold_metrics(rows)


def _category_section(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"status": "not_run", "reason": "category_audit_sheet.csv missing"}
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        return {"status": "not_run", "reason": "category_audit_sheet.csv is empty"}
    if any(not str(row.get("human_label") or "").strip() for row in rows):
        return {"status": "not_run", "reason": "category_audit_sheet.csv has unlabelled rows"}
    return category_audit_metrics(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark pinned sentiment models on hand-labelled gold data."
    )
    parser.add_argument("--gold", type=Path, default=Path("evaluation/gold_items.csv"))
    parser.add_argument("--labels", type=Path, default=Path("evaluation/labels_final.csv"))
    parser.add_argument("--relabels", type=Path, default=Path("evaluation/labels_relabel.csv"))
    parser.add_argument("--units", type=Path, default=Path("evaluation/units_gold.csv"))
    parser.add_argument("--pairs", type=Path, default=Path("evaluation/pairs_gold.csv"))
    parser.add_argument(
        "--category-audit",
        type=Path,
        default=Path("evaluation/category_audit_sheet.csv"),
    )
    parser.add_argument("--frame-weights", type=Path, default=Path("evaluation/frame_weights.json"))
    parser.add_argument("--models-dir", type=Path, default=Path("models"))
    parser.add_argument("--out", type=Path, default=Path("evaluation/results.json"))
    parser.add_argument("--markdown", type=Path, default=Path("evaluation/results.md"))
    parser.add_argument("--with-siebert", action="store_true")
    args = parser.parse_args()

    items = read_gold_items(args.gold)
    final_labels = _read_final_labels(args.labels)
    if set(final_labels) != {item.review_id for item in items}:
        raise ValueError("labels_final.csv must match gold_items.csv")
    frame = json.loads(args.frame_weights.read_text(encoding="utf-8"))
    frame_shares = {str(key): value for key, value in frame["star_shares"].items()}

    evaluable_items = [item for item in items if final_labels[item.review_id] != "mixed"]
    y_true = [final_labels[item.review_id] for item in evaluable_items]
    texts = [item.text for item in evaluable_items]
    stars = [item.star for item in evaluable_items]

    shipping = TransformerSentiment.load(args.models_dir)
    shipping_pred, shipping_latency = timed_predictions(shipping, texts)
    challenger = FiveClassSentimentAdapter.load(args.models_dir)
    challenger_pred, challenger_latency = timed_predictions(challenger, texts)
    baseline_pred = star_baseline(stars)

    shipping_record = _model_record(
        name="shipping_cardiffnlp",
        model_id=SENTIMENT_MODEL_ID,
        revision=SENTIMENT_MODEL_REVISION,
        size_bytes=_dir_size(sentiment_model_path(args.models_dir)),
        y_true=y_true,
        y_pred=shipping_pred,
        stars=stars,
        frame_shares=frame_shares,
        latency_per_100_ms=shipping_latency,
        eligible=False,
        license_name="existing shipping model",
    )
    challenger_record = _model_record(
        name="tabularisai_robust_sentiment",
        model_id=TABULARIS_MODEL_ID,
        revision=TABULARIS_MODEL_REVISION,
        size_bytes=_dir_size(args.models_dir / TABULARIS_MODEL_DIRNAME),
        y_true=y_true,
        y_pred=challenger_pred,
        stars=stars,
        frame_shares=frame_shares,
        latency_per_100_ms=challenger_latency,
        eligible=True,
        license_name="apache-2.0",
    )
    baseline_record = _model_record(
        name="star_rating_floor",
        model_id="star-rating-baseline",
        revision="fixed-rule-v1",
        size_bytes=0,
        y_true=y_true,
        y_pred=baseline_pred,
        stars=stars,
        frame_shares=frame_shares,
        latency_per_100_ms=0.0,
        eligible=False,
        license_name=None,
    )
    paired_ci = paired_negative_f1_difference_ci(y_true, shipping_pred, challenger_pred)
    switch_supported = paired_ci.get("low") is not None and float(paired_ci["low"]) > 0

    error_rows: list[dict[str, Any]] = []
    for item, truth, ship, challenge, baseline in zip(
        evaluable_items, y_true, shipping_pred, challenger_pred, baseline_pred, strict=True
    ):
        if ship != truth or challenge != truth:
            error_rows.append(
                {
                    "review_id": item.review_id,
                    "star": item.star,
                    "gold": truth,
                    "shipping": ship,
                    "tabularis": challenge,
                    "star_baseline": baseline,
                    "text": item.text[:300],
                }
            )
        if len(error_rows) >= 8:
            break

    references: list[dict[str, Any]] = []
    if args.with_siebert:
        siebert = BinarySentimentAdapter.load(args.models_dir)
        binary_items = [
            item
            for item in evaluable_items
            if final_labels[item.review_id] in {"negative", "positive"}
        ]
        binary_texts = [item.text for item in binary_items]
        binary_true = [final_labels[item.review_id] for item in binary_items]
        if not binary_items:
            raise ValueError("SiEBERT reference has no positive/negative evaluable items")
        import time

        started = time.perf_counter()
        binary_pred = siebert.predict_labels(binary_texts)
        elapsed = time.perf_counter() - started
        references.append(
            {
                "name": "siebert_binary_reference",
                "model_id": SIEBERT_MODEL_ID,
                "revision": SIEBERT_MODEL_REVISION,
                "size_bytes": _dir_size(args.models_dir / SIEBERT_MODEL_DIRNAME),
                "scope": "positive_negative_only",
                "n": len(binary_items),
                "accuracy": sum(
                    left == right for left, right in zip(binary_true, binary_pred, strict=True)
                )
                / len(binary_items),
                "latency_per_100_ms": elapsed / len(binary_items) * 100.0 * 1000.0,
                "eligible_challenger": False,
            }
        )

    relabel: dict[str, Any]
    if args.relabels.exists():
        relabel = annotator_relabel_statistics(final_labels, _read_simple_labels(args.relabels))
    else:
        relabel = {"status": "not_run", "reason": "labels_relabel.csv missing"}

    benchmark = {
        "status": "run",
        "gold_seed": GOLD_SEED,
        "evaluable_n": len(evaluable_items),
        "mixed_n": len(items) - len(evaluable_items),
        "neutral_support": sum(label == "neutral" for label in y_true),
        "neutral_note": (
            "The neutral class is too small for a reliable standalone performance conclusion."
        ),
        "models": [shipping_record, challenger_record, baseline_record],
        "references": references,
        "paired_tabularis_minus_shipping_negative_f1_ci95": paired_ci,
        "decision_rule": (
            "Switch only if an eligible licence-clean 3-class challenger has a "
            "paired-bootstrap negative-F1 difference CI excluding zero in its favour; "
            "on a tie keep the better-documented model."
        ),
        "switch_supported": switch_supported,
        "error_analysis": error_rows,
    }
    results: dict[str, Any] = {
        "benchmark": benchmark,
        "annotator_relabel": relabel,
        "complaint_units": _unit_section(args.units, shipping, Settings()),
        "theme_threshold": _pair_section(args.pairs),
        "issue_categories": _category_section(args.category_audit),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    args.markdown.write_text(render_results_markdown(results), encoding="utf-8", newline="\n")
    print(
        json.dumps(
            {
                "results": str(args.out),
                "markdown": str(args.markdown),
                "evaluable_n": len(evaluable_items),
                "mixed_n": len(items) - len(evaluable_items),
            }
        )
    )


if __name__ == "__main__":
    main()
