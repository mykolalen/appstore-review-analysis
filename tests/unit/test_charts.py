"""Chart-data extraction and PNG rendering for the additional report charts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from appstore_review_analysis.report.charts import (
    CATEGORY_PRECISION_TARGET,
    category_precision_data,
    complaint_funnel_data,
    negative_phrases_data,
    render_all_charts,
    summary_card_data,
)

PNG_SIGNATURE = bytes.fromhex("89504e470d0a1a0a")


def _analysis() -> dict[str, Any]:
    return {
        "app": {"name": "Demo App"},
        "sampling": {"storefront": "us", "seed": 7, "reachable": 1234},
        "metrics": {
            "n": 100,
            "mean": 3.2,
            "mean_ci95": {"low": 2.8, "high": 3.6},
            "distribution": {
                str(star): {
                    "count": 20,
                    "percentage": 0.2,
                    "ci95": {"low": 0.13, "high": 0.29},
                }
                for star in range(1, 6)
            },
            "periods": [
                {"label": "2026", "n": 60, "mean": 4.0, "mean_ci95": {"low": 3.5, "high": 4.5}},
                {"label": "2025", "n": 40, "mean": 2.0, "mean_ci95": {"low": 1.5, "high": 2.5}},
            ],
        },
        "sentiment": {
            "distribution": {
                "negative": {"count": 40, "percentage": 0.4, "ci95": {"low": 0.3, "high": 0.5}},
                "neutral": {"count": 10, "percentage": 0.1, "ci95": {"low": 0.05, "high": 0.18}},
                "positive": {"count": 50, "percentage": 0.5, "ci95": {"low": 0.4, "high": 0.6}},
            }
        },
        "preprocessing": {"n_all": 100, "n_complaint_reviews": 40},
        "keywords": {
            "n_negative": 40,
            "common": [
                {"phrase": "pay", "support_count": 10},
                {"phrase": "scam", "support_count": 8},
                {"phrase": "", "support_count": 7},
                {"phrase": "refund", "support_count": 0},
            ],
        },
        "insights": {
            "issue_categories": {
                "denominator": 40,
                "items": [
                    {
                        "category_id": "pricing_paywall",
                        "label": "Pricing and paywall",
                        "review_count": 12,
                        "share": 0.3,
                        "ci95": {"low": 0.18, "high": 0.45},
                    },
                    {
                        "category_id": "scam_trust",
                        "label": "Scam and trust",
                        "review_count": 8,
                        "share": 0.2,
                        "ci95": {"low": 0.1, "high": 0.35},
                    },
                ],
                "not_categorised": {"review_count": 6, "denominator": 40},
            },
            "areas_of_improvement": [
                {
                    "source": "theme",
                    "area": "Ignored theme",
                    "complaint_reviews": {"count": 3, "total": 40, "share": 0.075},
                },
                {
                    "source": "issue_category",
                    "area": "Pricing and paywall",
                    "complaint_reviews": {"count": 12, "total": 40, "share": 0.3},
                },
            ],
        },
    }


def _audit(*, pricing_correct: int) -> dict[str, Any]:
    return {
        "issue_categories": {
            "status": "run",
            "categories": [
                {
                    "category": "scam_trust",
                    "n": 10,
                    "correct": 10,
                    "precision": {"value": 1.0, "low": 0.72, "high": 1.0},
                },
                {
                    "category": "pricing_paywall",
                    "n": 10,
                    "correct": pricing_correct,
                    "precision": {
                        "value": pricing_correct / 10,
                        "low": 0.3,
                        "high": 0.9,
                    },
                },
            ],
        }
    }


def test_negative_phrases_use_share_of_negative_reviews_and_skip_blank_rows() -> None:
    data = negative_phrases_data(_analysis())

    assert data["labels"] == ["pay", "scam"]
    assert data["counts"] == [10, 8]
    assert data["values"] == [0.25, 0.2]
    assert data["total"] == 40


def test_complaint_funnel_subtracts_uncategorised_reviews() -> None:
    data = complaint_funnel_data(_analysis())

    assert data["counts"] == [100, 40, 34]
    assert data["values"] == [1.0, 0.4, 0.34]


def test_category_precision_is_none_unless_the_audit_ran() -> None:
    assert category_precision_data(None) is None
    assert category_precision_data({}) is None
    assert category_precision_data({"issue_categories": {"status": "not_run"}}) is None


def test_category_precision_sorts_strongest_first_and_uses_readable_names() -> None:
    data = category_precision_data(
        _audit(pricing_correct=6), {"pricing_paywall": "Pricing and paywall"}
    )

    assert data is not None
    assert data["labels"] == ["Scam trust", "Pricing and paywall"]
    assert data["values"] == [1.0, 0.6]
    assert data["correct"] == [10, 6]
    assert data["total"] == 20
    assert CATEGORY_PRECISION_TARGET == 0.80


def test_summary_card_reports_headline_numbers_and_the_top_issue_category() -> None:
    card = summary_card_data(_analysis())

    assert card["title"] == "Demo App"
    assert card["subtitle"] == "App Store review analysis · US · seed 7"
    assert [tile["value"] for tile in card["tiles"]] == ["100", "3.20", "40%", "30%"]
    assert card["tiles"][0]["detail"] == "of 1,234 reachable"
    assert card["tiles"][3]["label"] == "top issue: Pricing and paywall"
    assert card["tiles"][3]["detail"] == "12 of 40 complaint reviews"


def test_summary_card_skips_tiles_without_data() -> None:
    card = summary_card_data({"app": {}, "metrics": {"n": 5}})

    assert card["title"] == "App Store app"
    assert [tile["value"] for tile in card["tiles"]] == ["5"]


def test_render_all_charts_writes_valid_pngs_and_adds_precision_chart_only_after_an_audit(
    tmp_path: Path,
) -> None:
    without_audit = render_all_charts(_analysis(), tmp_path / "plain")
    with_audit = render_all_charts(_analysis(), tmp_path / "audited", _audit(pricing_correct=6))

    assert "category_precision" not in without_audit
    assert set(with_audit) == set(without_audit) | {"category_precision"}
    for path in with_audit.values():
        payload = path.read_bytes()
        assert payload.startswith(PNG_SIGNATURE)
        assert len(payload) > 1000
