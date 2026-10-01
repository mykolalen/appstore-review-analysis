from __future__ import annotations

import json
import runpy
import warnings
from datetime import UTC, datetime
from pathlib import Path

import pytest

from appstore_review_analysis.domain import Review
from appstore_review_analysis.evaluation import (
    STRATUM_COUNTS,
    GoldItem,
    annotator_relabel_statistics,
    assert_pinned_model,
    build_gold_items,
    category_audit_metrics,
    classification_metrics,
    label_sheet_rows,
    pair_threshold_metrics,
    read_gold_items,
    render_results_markdown,
    unit_metrics,
    write_gold_items,
)


def _review(review_id: str, rating: int, rank: int) -> Review:
    return Review(
        source_review_id=review_id,
        rank=rank,
        country="us",
        title=f"Title {review_id}",
        body=f"This is a sufficiently long English review body number {review_id}.",
        rating=rating,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        is_edited=False,
        vote_count=0,
        vote_sum=0,
        has_developer_response=False,
        developer_response_id=None,
        developer_response_date=None,
        app_version="1.0",
        provider="itunes",
    )


def test_gold_draw_fills_strata_and_excludes_demo_ids() -> None:
    root = Path(__file__).resolve().parents[2]
    snapshot = json.loads(
        (root / "data/fixtures/nebula_us_seed42.snapshot.json").read_text(encoding="utf-8")
    )
    demo_ids = {str(row["source_review_id"]) for row in snapshot["reviews"]}
    population: list[Review] = []
    rank = 0
    for star in range(1, 6):
        for index in range(70):
            population.append(_review(f"synthetic-{star}-{index}", star, rank))
            rank += 1
    demo = snapshot["reviews"][0]
    population.append(_review(str(demo["source_review_id"]), int(demo["rating"]), rank))

    items = build_gold_items(
        population,
        excluded_review_ids=demo_ids,
        language_filter=lambda _review: True,
    )

    assert len(items) == 150
    assert not ({item.review_id for item in items} & demo_ids)
    assert {
        star: sum(item.star == star for item in items) for star in range(1, 6)
    } == STRATUM_COUNTS


def test_gold_items_round_trip_without_extra_columns(tmp_path: Path) -> None:
    items = [
        GoldItem(review_id="r1", rank=3, stratum=1, star=1, text="Bad, really bad."),
        GoldItem(review_id="r2", rank=9, stratum=5, star=5, text="Great."),
    ]
    path = tmp_path / "gold_items.csv"
    write_gold_items(path, items)

    assert path.read_text(encoding="utf-8").splitlines()[0] == "review_id,rank,stratum,star,text"
    assert read_gold_items(path) == items


def test_label_sheet_is_text_only_with_empty_labels_in_a_stable_order() -> None:
    items = [
        GoldItem(review_id=f"r{index}", rank=index, stratum=1, star=1, text=f"text {index}")
        for index in range(150)
    ]
    rows = label_sheet_rows(items)

    assert len(rows) == 150
    assert {row["review_id"] for row in rows} == {item.review_id for item in items}
    assert all(set(row) == {"review_id", "text", "human_label"} for row in rows)
    assert all(row["human_label"] == "" for row in rows)
    assert rows == label_sheet_rows(list(reversed(items)))


def test_classification_metrics_match_hand_calculation() -> None:
    metrics = classification_metrics(
        ["negative", "negative", "neutral", "positive"],
        ["negative", "positive", "neutral", "positive"],
    )
    assert metrics["accuracy"] == pytest.approx(0.75)
    assert metrics["negative_f1"] == pytest.approx(2 / 3)
    assert metrics["macro_f1"] == pytest.approx((2 / 3 + 1.0 + 2 / 3) / 3)
    assert metrics["confusion_matrix"] == [[1, 0, 1], [0, 1, 0], [0, 0, 1]]


def test_optional_evaluation_helpers() -> None:
    units = unit_metrics(
        [
            {"id": "1", "text": "a", "human_label": "complaint", "model_label": "complaint"},
            {"id": "2", "text": "b", "human_label": "complaint", "model_label": "not_complaint"},
            {"id": "3", "text": "c", "human_label": "not_complaint", "model_label": "complaint"},
        ]
    )
    assert units["precision"]["value"] == pytest.approx(0.5)  # type: ignore[index]
    assert units["recall"]["value"] == pytest.approx(0.5)  # type: ignore[index]

    threshold = pair_threshold_metrics(
        [
            {"distance": "0.20", "human_label": "same_issue"},
            {"distance": "0.25", "human_label": "same_issue"},
            {"distance": "0.30", "human_label": "different_issue"},
        ]
    )
    assert threshold["status"] == "run"
    assert threshold["selected_threshold"] is not None


def test_category_audit_metrics_are_per_category_wilson_precision() -> None:
    result = category_audit_metrics(
        [
            {
                "category": "billing_charges",
                "review_id": "r1",
                "matched_phrase": "charged",
                "sentence": "I was charged.",
                "human_label": "correct",
            },
            {
                "category": "billing_charges",
                "review_id": "r2",
                "matched_phrase": "charge",
                "sentence": "No relevant charge complaint.",
                "human_label": "incorrect",
            },
            {
                "category": "refunds",
                "review_id": "r3",
                "matched_phrase": "refund",
                "sentence": "Refund never arrived.",
                "human_label": "correct",
            },
        ]
    )
    assert result["status"] == "run"
    categories = {row["category"]: row for row in result["categories"]}  # type: ignore[index]
    billing_precision = categories["billing_charges"]["precision"]  # type: ignore[index]
    assert billing_precision["value"] == pytest.approx(0.5)
    assert categories["refunds"]["precision"]["value"] == pytest.approx(1.0)  # type: ignore[index]
    assert result["overall_precision"]["value"] == pytest.approx(2 / 3)  # type: ignore[index]


def test_category_audit_rejects_missing_or_invalid_human_labels() -> None:
    base = {
        "category": "refunds",
        "review_id": "r1",
        "matched_phrase": "refund",
        "sentence": "Refund never arrived.",
    }
    with pytest.raises(ValueError, match="missing human_label"):
        category_audit_metrics([{**base, "human_label": ""}])
    with pytest.raises(ValueError, match="correct.*incorrect"):
        category_audit_metrics([{**base, "human_label": "maybe"}])


def test_category_audit_validator_is_optional_when_sheet_is_absent(tmp_path: Path) -> None:
    validator = runpy.run_path(str(Path("evaluation/validate_category_audit.py")))
    section = validator["_section"]

    result = section(tmp_path / "missing_category_audit_sheet.csv")
    assert result == {
        "status": "not_run",
        "reason": "category_audit_sheet.csv missing",
    }


def test_annotator_relabel_perfect_agreement_is_warning_free_and_finite() -> None:
    original = {"a": "negative", "b": "mixed", "c": "positive"}

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        stats = annotator_relabel_statistics(original, original)

    assert caught == []
    assert stats["kappa"] == pytest.approx(1.0)
    ci = stats["bootstrap_ci95"]
    assert isinstance(ci, dict)
    assert ci["low"] == pytest.approx(1.0)
    assert ci["high"] == pytest.approx(1.0)
    assert ci["reason"] == "degenerate_perfect_agreement"


def test_annotator_relabel_single_class_does_not_emit_nan() -> None:
    labels = {"a": "negative", "b": "negative", "c": "negative"}
    stats = annotator_relabel_statistics(labels, labels)

    assert stats["kappa"] is None
    ci = stats["bootstrap_ci95"]
    assert isinstance(ci, dict)
    assert ci["low"] is None
    assert ci["high"] is None
    assert ci["reason"] == "undefined_single_class"


def test_results_markdown_publishes_metrics_and_threshold_table() -> None:
    results = {
        "benchmark": {
            "status": "run",
            "evaluable_n": 2,
            "mixed_n": 0,
            "neutral_support": 0,
            "neutral_note": "Neutral support is small.",
            "decision_rule": "Pre-written rule.",
            "switch_supported": False,
            "paired_tabularis_minus_shipping_negative_f1_ci95": {
                "low": -0.1,
                "high": 0.2,
                "reason": None,
            },
            "models": [
                {
                    "name": "shipping",
                    "revision": "abc",
                    "size_bytes": 1024 * 1024,
                    "latency_per_100_ms": 10.0,
                    "metrics": {
                        "accuracy": 0.5,
                        "macro_f1": 0.4,
                        "negative_f1": 0.6,
                        "prevalence_reweighted_accuracy": 0.55,
                        "ci95": {
                            "accuracy": {"low": 0.2, "high": 0.8, "reason": None},
                            "macro_f1": {"low": 0.1, "high": 0.7, "reason": None},
                            "negative_f1": {"low": 0.3, "high": 0.9, "reason": None},
                        },
                        "per_class": {
                            label: {
                                "precision": 0.5,
                                "recall": 0.5,
                                "f1": 0.5,
                                "support": 1,
                            }
                            for label in ("negative", "neutral", "positive")
                        },
                        "confusion_matrix": [[1, 0, 0], [0, 0, 0], [0, 1, 0]],
                    },
                }
            ],
            "references": [],
            "error_analysis": [],
        },
        "annotator_relabel": {"status": "not_run", "reason": "missing"},
        "complaint_units": {"status": "not_run", "reason": "missing"},
        "issue_categories": {
            "status": "run",
            "categories": [
                {
                    "category": "refunds",
                    "n": 2,
                    "correct": 2,
                    "precision": {
                        "successes": 2,
                        "n": 2,
                        "value": 1.0,
                        "low": 0.342,
                        "high": 1.0,
                    },
                }
            ],
            "note": "Human precision audit.",
        },
        "theme_threshold": {
            "status": "run",
            "minimum_precision": 0.8,
            "selected_threshold": 0.4,
            "table": [
                {
                    "threshold": 0.4,
                    "predicted_same": 10,
                    "true_same": 8,
                    "precision": 0.8,
                    "eligible": True,
                }
            ],
        },
    }
    markdown = render_results_markdown(results)
    assert "Negative F1 (95% CI)" in markdown
    assert "Anchoring" not in markdown
    assert "Blind" not in markdown
    assert "Reweighted accuracy" in markdown
    assert "Confusion matrix" in markdown
    assert "Selected threshold: **0.40**" in markdown
    assert "## Issue-category precision audit" in markdown
    assert "| refunds | 2 | 2 |" in markdown
    assert "| 0.40 | 10 | 8 | 0.800 | True |" in markdown


def test_evaluation_model_pin_marker_is_required(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not verified"):
        assert_pinned_model(tmp_path, "abc")
    (tmp_path / ".pinned_revision").write_text("abc\n", encoding="utf-8")
    assert_pinned_model(tmp_path, "abc")


def test_category_audit_validator_accepts_an_excel_utf8_bom_sheet(tmp_path: Path) -> None:
    # Excel's "CSV UTF-8" format prepends a BOM; the header must still be recognised.
    sheet = tmp_path / "sheet.csv"
    sheet.write_text(
        "category,review_id,matched_phrase,sentence,human_label\n"
        "billing_charges,r1,charged,I was charged twice.,correct\n",
        encoding="utf-8-sig",
    )
    validator = runpy.run_path(str(Path("evaluation/validate_category_audit.py")))

    result = validator["_section"](sheet)

    assert result["status"] == "run"
    assert result["n"] == 1
