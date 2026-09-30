from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from appstore_review_analysis.analysis.sentiment import FakeSentiment
from appstore_review_analysis.analysis.themes import (
    EMBEDDING_MODEL_ALLOW_PATTERNS,
    EMBEDDING_MODEL_ID,
    EMBEDDING_MODEL_REVISION,
    FakeEmbedder,
    analyse_themes,
    download_embedding_model,
)
from appstore_review_analysis.domain import AnalysedReview


def _row(index: int, text: str, *, days_ago: int = 10) -> AnalysedReview:
    return AnalysedReview(
        source_review_id=f"review-{index:02d}",
        rank=index,
        country="us",
        title="",
        body=text,
        rating=1,
        created_at=datetime(2026, 9, 29, tzinfo=UTC) - timedelta(days=days_ago),
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


def _rows(count: int) -> list[AnalysedReview]:
    rows: list[AnalysedReview] = []
    for index in range(count):
        if index % 2:
            text = "bad crash broken problem"
        else:
            text = "bad charged refund subscription problem"
        rows.append(_row(index, text, days_ago=10 if index < count - 1 else 500))
    return rows


def test_adaptive_policy_under_five_units_skips_clustering() -> None:
    result, _timings = analyse_themes(
        _rows(4),
        sentiment=FakeSentiment(),
        embedder=FakeEmbedder(),
        reference_date=datetime(2026, 9, 29, tzinfo=UTC),
    )
    assert result["status"] == "insufficient_units"
    assert result["n_units"] == 4
    assert result["items"] == []
    assert result["other"]["unit_count"] == 4


def test_adaptive_policy_5_to_14_keeps_support_two_clusters() -> None:
    result, _timings = analyse_themes(
        _rows(6),
        sentiment=FakeSentiment(),
        embedder=FakeEmbedder(),
        reference_date=datetime(2026, 9, 29, tzinfo=UTC),
    )
    assert result["minimum_cluster_support"] == 2
    assert len(result["items"]) == 2
    assert sum(item["review_count"] for item in result["items"]) == 6


def test_adaptive_policy_15_plus_requires_support_three_and_is_recomputable() -> None:
    result, _timings = analyse_themes(
        _rows(18),
        sentiment=FakeSentiment(),
        embedder=FakeEmbedder(),
        reference_date=datetime(2026, 9, 29, tzinfo=UTC),
    )
    assert result["minimum_cluster_support"] == 3
    assert result["n_complaint_reviews"] == 18
    for item in result["items"]:
        evidence_ids = item["evidence_review_ids"]
        assert item["review_count"] == len(set(evidence_ids))
        share = item["share_of_complaint_reviews"]
        assert share["numerator"] == item["review_count"]
        assert share["denominator"] == 18
        assert share["value"] == item["review_count"] / 18
        analysable_share = item["share_of_analysable"]
        assert analysable_share["denominator"] == 18
        assert analysable_share["value"] == item["review_count"] / 18
        representatives = item["representative_units"]
        ordering = [
            (rep["distance_to_centroid"], rep["review_id"], rep["unit_id"])
            for rep in representatives
        ]
        assert ordering == sorted(ordering)


def test_fake_embedder_returns_normalized_vectors() -> None:
    vectors = FakeEmbedder().encode(["bad refund charged", "bad crash broken"])
    assert vectors.shape == (2, 5)
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0)


def test_embedding_download_uses_pinned_allowlist(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from appstore_review_analysis.analysis import themes as themes_module

    calls: list[dict[str, object]] = []

    def fake_snapshot_download(**kwargs: object) -> str:
        calls.append(dict(kwargs))
        return str(kwargs["local_dir"])

    monkeypatch.setattr(themes_module, "snapshot_download", fake_snapshot_download)
    first = download_embedding_model(tmp_path)
    second = download_embedding_model(tmp_path)
    assert first == second
    assert len(calls) == 2
    for call in calls:
        assert call["repo_id"] == EMBEDDING_MODEL_ID
        assert call["revision"] == EMBEDDING_MODEL_REVISION
        assert call["allow_patterns"] == EMBEDDING_MODEL_ALLOW_PATTERNS


def test_positive_review_negative_score_gate_removes_weak_false_positive() -> None:
    from appstore_review_analysis.analysis.sentiment import SentimentResult
    from appstore_review_analysis.analysis.themes import complaint_units

    rows = [
        _row(1, "I am amazed at this app.", days_ago=1).model_copy(update={"rating": 5}),
        _row(
            2,
            "The billing failure keeps charging me despite cancellation.",
            days_ago=1,
        ).model_copy(update={"rating": 5}),
        _row(3, "Scam.", days_ago=1).model_copy(update={"rating": 1}),
    ]

    class ScriptedSentiment:
        def predict(self, texts: list[str]) -> list[SentimentResult]:
            scores = {
                "I am amazed at this app.": 0.81,
                "The billing failure keeps charging me despite cancellation.": 0.95,
                "Scam.": 0.65,
            }
            return [
                SentimentResult(
                    label="negative",
                    class_scores={
                        "negative": scores[text],
                        "neutral": 1.0 - scores[text],
                        "positive": 0.0,
                    },
                    model_id="scripted",
                    model_revision="test",
                )
                for text in texts
            ]

    gate = complaint_units(rows, ScriptedSentiment())
    assert [unit.text for unit in gate.units] == [
        "The billing failure keeps charging me despite cancellation.",
        "Scam.",
    ]
    assert gate.negative_label_candidates == 3
    assert gate.removed_positive_reviews == 1
    assert gate.removed_other_reviews == 0
    payload = gate.as_dict()
    assert payload["positive_review_min_negative_score"] == 0.85
    assert payload["other_review_min_negative_score"] == 0.5
    assert payload["removed_by_rule"] == {
        "rating_4_5_below_threshold": 1,
        "rating_1_3_below_threshold": 0,
    }


def test_theme_output_exposes_coverage_gate_and_diagnostics() -> None:
    result, _timings = analyse_themes(
        _rows(8),
        sentiment=FakeSentiment(),
        embedder=FakeEmbedder(),
        reference_date=datetime(2026, 9, 29, tzinfo=UTC),
    )
    coverage = result["coverage"]
    assert coverage["units_total"] == result["n_units"]
    assert coverage["units_clustered"] <= coverage["units_total"]
    assert 0.0 <= coverage["unit_share"] <= 1.0
    assert coverage["complaint_reviews_in_themes"] <= coverage["n_complaint_reviews"]
    assert 0.0 <= coverage["review_share"] <= 1.0

    gate = result["unit_gate"]
    assert gate["positive_review_min_negative_score"] == 0.85
    assert gate["other_review_min_negative_score"] == 0.5
    assert gate["kept"] == result["n_units"]

    cluster_sizes = result["diagnostics"]["cluster_sizes"]
    assert set(cluster_sizes) == {"0.30", "0.40", "0.50", "0.60"}


def test_representatives_prefer_a_complete_sentence_over_a_short_snippet() -> None:
    from appstore_review_analysis.analysis.themes import ComplaintUnit, _representatives

    units = [
        ComplaintUnit("u1", "r1", "Scam.", "scam", 1, None, 0.99),
        ComplaintUnit(
            "u2",
            "r2",
            "The subscription charged me again after I cancelled it.",
            "the subscription charged me again after i cancelled it",
            1,
            None,
            0.99,
        ),
        ComplaintUnit(
            "u3",
            "r3",
            "Billing support never returned the money they charged.",
            "billing support never returned the money they charged",
            1,
            None,
            0.99,
        ),
    ]
    vectors = np.asarray([[1.0, 0.0], [0.8, 0.2], [0.75, 0.25]], dtype=np.float64)
    representatives = _representatives(units, vectors)
    assert len(representatives[0]["excerpt"]) >= 40
    assert representatives[0]["excerpt"] != "Scam."


def test_threshold_selector_chooses_largest_grid_value_with_required_precision() -> None:
    from appstore_review_analysis.analysis.themes import select_distance_threshold

    pairs = [
        (0.18, True),
        (0.22, True),
        (0.27, True),
        (0.32, False),
        (0.36, True),
        (0.42, False),
    ]
    result = select_distance_threshold(pairs)
    assert result["selected_threshold"] == 0.40
    table = {row["threshold"]: row for row in result["table"]}
    assert table[0.40]["precision"] == 0.8
    assert table[0.45]["precision"] < 0.8
