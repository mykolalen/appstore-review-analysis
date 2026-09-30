from __future__ import annotations

from datetime import UTC, datetime

from appstore_review_analysis.analysis.evidence import build_areas_of_improvement
from appstore_review_analysis.domain import AnalysedReview


def _row(review_id: str, text: str) -> AnalysedReview:
    return AnalysedReview(
        source_review_id=review_id,
        rank=0,
        country="us",
        title="",
        body=text,
        rating=1,
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
        is_edited=False,
        vote_count=0,
        vote_sum=0,
        has_developer_response=False,
        developer_response_id=None,
        developer_response_date=None,
        provider="fixture",
        analysis_text=text,
        lexical_text=text.lower(),
        language="en",
        analysable=True,
        sentiment_label="negative",
    )


def test_areas_exist_when_themes_exist_and_recent_comes_first() -> None:
    rows = [_row("r1", "charged again"), _row("r2", "app crashed")]
    themes = {
        "items": [
            {
                "theme_id": "old",
                "review_count": 1,
                "share_of_complaint_reviews": {
                    "value": 0.5,
                    "denominator": 2,
                    "ci95": {"low": 0.1, "high": 0.9},
                },
                "mean_star_rating": 1.0,
                "top_phrases": [{"phrase": "old billing"}],
                "newest_date": "2024-01-01T00:00:00+00:00",
                "share_last_12_months": 0.0,
                "historical": True,
                "representative_units": [{"review_id": "r1", "excerpt": "charged again"}],
                "evidence_review_ids": ["r1"],
            },
            {
                "theme_id": "recent",
                "review_count": 1,
                "share_of_complaint_reviews": {
                    "value": 0.5,
                    "denominator": 2,
                    "ci95": {"low": 0.1, "high": 0.9},
                },
                "mean_star_rating": 1.0,
                "top_phrases": [{"phrase": "app crash"}],
                "newest_date": "2026-09-01T00:00:00+00:00",
                "share_last_12_months": 1.0,
                "historical": False,
                "representative_units": [{"review_id": "r2", "excerpt": "app crashed"}],
                "evidence_review_ids": ["r2"],
            },
        ]
    }
    result = build_areas_of_improvement(themes=themes, keywords={}, rows=rows)
    assert result["status"] == "ok"
    areas = result["areas_of_improvement"]
    assert [item["theme_id"] for item in areas] == ["recent", "old"]
    assert all(item["evidence_review_ids"] for item in areas)
    assert all(str(item["text"]).startswith("Investigate") for item in areas)


def test_area_label_removes_subsumed_theme_phrase_even_with_different_support() -> None:
    rows = [_row("r1", "This is a scam and I was charged after cancellation.")]
    themes = {
        "items": [
            {
                "theme_id": "theme_01",
                "unit_count": 9,
                "review_count": 1,
                "share_of_complaint_reviews": {
                    "value": 1.0,
                    "denominator": 1,
                    "ci95": {"low": 0.2, "high": 1.0},
                },
                "mean_star_rating": 1.0,
                "top_phrases": [
                    {"phrase": "scam", "support_count": 9},
                    {"phrase": "a scam", "support_count": 2},
                ],
                "newest_date": "2026-09-01T00:00:00+00:00",
                "share_last_12_months": 1.0,
                "historical": False,
                "representative_units": [
                    {
                        "review_id": "r1",
                        "excerpt": "This is a scam and I was charged after cancellation.",
                    }
                ],
                "evidence_review_ids": ["r1"],
            }
        ]
    }
    result = build_areas_of_improvement(themes=themes, keywords={}, rows=rows)
    assert result["areas_of_improvement"][0]["area"] == "scam"


def test_phrase_fallback_is_non_empty_when_no_cluster_survives() -> None:
    rows = [_row("r1", "refund trap")]
    keywords = {
        "n_negative": 5,
        "distinctive": [
            {
                "phrase": "refund trap",
                "support_count": 3,
                "negative_reviews": {"count": 3, "total": 5},
                "example_review_id": "r1",
                "newest_date": "2026-09-01T00:00:00+00:00",
                "share_last_12_months": 1.0,
            }
        ],
        "common": [],
    }
    result = build_areas_of_improvement(
        themes={"items": []},
        keywords=keywords,
        rows=rows,
    )
    assert result["status"] == "ok"
    assert result["areas_of_improvement"][0]["area"] == "refund trap"


def test_theme_without_displayable_phrase_gets_explicit_unlabelled_name() -> None:
    rows = [_row("r1", "This app is frustrating and fails every day.")]
    themes = {
        "items": [
            {
                "theme_id": "theme_01",
                "unit_count": 3,
                "review_count": 1,
                "share_of_complaint_reviews": {
                    "value": 1.0,
                    "denominator": 1,
                    "ci95": {"low": 0.2, "high": 1.0},
                },
                "mean_star_rating": 1.0,
                "top_phrases": [],
                "newest_date": "2026-09-01T00:00:00+00:00",
                "share_last_12_months": 1.0,
                "historical": False,
                "representative_units": [
                    {
                        "review_id": "r1",
                        "excerpt": "This app is frustrating and fails every day.",
                    }
                ],
                "evidence_review_ids": ["r1"],
            }
        ]
    }
    result = build_areas_of_improvement(themes=themes, keywords={}, rows=rows)
    assert result["areas_of_improvement"][0]["area"] == "Unlabelled complaint group (3 units)"
