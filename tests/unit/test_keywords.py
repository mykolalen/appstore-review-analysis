from __future__ import annotations

import math
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from appstore_review_analysis.analysis.keywords import (
    GENERIC_APP_TOKENS,
    _fightin_words_core,
    _ngrams,
    analyse_keywords,
    app_display_tokens,
    collapse_subsumed,
    displayable_phrase,
)
from appstore_review_analysis.analysis.pipeline import analyse_collection
from appstore_review_analysis.analysis.sentiment import FakeSentiment
from appstore_review_analysis.analysis.themes import FakeEmbedder
from appstore_review_analysis.collection.fixture import FixtureProvider
from appstore_review_analysis.domain import AnalysedReview

ROOT = Path(__file__).resolve().parents[2]


class _EnglishIdentifier:
    def classify(self, _text: str) -> tuple[str, float]:
        return "en", 0.99


def _row(index: int, text: str, label: str, *, days_ago: int = 10) -> AnalysedReview:
    return AnalysedReview(
        source_review_id=f"r{index}",
        rank=index,
        country="us",
        title="",
        body=text,
        rating=1 if label == "negative" else 5,
        created_at=datetime(2026, 9, 29, tzinfo=UTC) - timedelta(days=days_ago),
        is_edited=False,
        vote_count=0,
        vote_sum=0,
        has_developer_response=False,
        developer_response_id=None,
        developer_response_date=None,
        provider="fixture",
        analysis_text=text,
        lexical_text=text,
        language="en",
        analysable=True,
        sentiment_label=label,  # type: ignore[arg-type]
    )


def test_common_and_distinctive_tables_separate_frequency_from_contrast() -> None:
    rows: list[AnalysedReview] = []
    for index in range(10):
        text = "shared phrase neutral"
        if index < 6:
            text += " refund trap"
        rows.append(_row(index, text, "negative"))
    for index in range(10, 20):
        text = "shared phrase neutral" if index < 18 else "ordinary useful"
        rows.append(_row(index, text, "positive"))

    result = analyse_keywords(
        rows,
        app_name="Nebula",
        reference_date=datetime(2026, 9, 29, tzinfo=UTC),
    )

    assert result["status"] == "ok"
    common = result["common"]
    distinctive = result["distinctive"]
    assert isinstance(common, list)
    assert isinstance(distinctive, list)
    assert common[0]["phrase"] == "shared phrase neutral"
    assert distinctive[0]["phrase"] in {"refund trap", "neutral refund trap"}
    assert not any(item["phrase"] == "shared phrase neutral" for item in distinctive[:3])


def test_fightin_words_matches_hand_computation() -> None:
    group = Counter({"refund": 4, "app": 5})
    rest = Counter({"refund": 1, "app": 5})
    ranked = dict(_fightin_words_core(group, rest, a0=100.0))

    n_group = sum(group.values())
    n_rest = sum(rest.values())
    n_total = n_group + n_rest
    a_w = 100.0 * 5 / n_total
    delta = math.log((4 + a_w) / (n_group + 100 - 4 - a_w)) - math.log(
        (1 + a_w) / (n_rest + 100 - 1 - a_w)
    )
    variance = 1 / (4 + a_w) + 1 / (1 + a_w)
    expected = delta / math.sqrt(variance)

    assert ranked["refund"] == expected


def test_lexical_vocabulary_preserves_not_and_has_no_contraction_fragments() -> None:
    phrases = _ngrams("i do not want this and cannot get money back")
    assert "not" in phrases
    assert "cannot" in phrases
    assert "money back" in phrases
    for fragment in {"don", "didn", "t", "s"}:
        assert fragment not in phrases


def test_display_filter_requires_a_content_token_and_keeps_negated_content() -> None:
    blocked = app_display_tokens("Nebula: Spiritual Guidance")
    for phrase in [
        "the and",
        "not",
        "no",
        "app",
        "this app",
        "nebula app",
        "just get",
        "when then",
        "do not",
        "only",
        "up",
        "2",
        "about",
        "after",
        "all",
        "before",
        "i am",
        "give",
        "what",
    ]:
        assert not displayable_phrase(phrase, blocked), phrase

    for phrase in ["not working", "cannot cancel", "no refund", "refund", "billing issue"]:
        assert displayable_phrase(phrase, blocked), phrase

    assert {"app", "nebula", "astrology"} <= GENERIC_APP_TOKENS


def test_subsumption_collapses_equal_support_in_the_requested_direction() -> None:
    rows = [
        {"phrase": "pay", "support_count": 4},
        {"phrase": "to pay", "support_count": 4},
        {"phrase": "hard to pay", "support_count": 4},
        {"phrase": "refund", "support_count": 3},
    ]
    longer = collapse_subsumed(rows, prefer_longer=True)
    shorter = collapse_subsumed(rows, prefer_longer=False)
    assert [row["phrase"] for row in longer] == ["hard to pay", "refund"]
    assert [row["phrase"] for row in shorter] == ["pay", "refund"]


def test_theme_label_subsumption_keeps_shorter_phrase_even_when_support_differs() -> None:
    rows = [
        {"phrase": "scam", "support_count": 9},
        {"phrase": "a scam", "support_count": 2},
        {"phrase": "billing charge", "support_count": 4},
    ]
    collapsed = collapse_subsumed(
        rows,
        prefer_longer=False,
        same_support_only=False,
    )
    assert [row["phrase"] for row in collapsed] == ["scam", "billing charge"]


def test_support_filter_and_subsumed_phrase_collapse() -> None:
    rows = [
        _row(1, "refund trap now", "negative"),
        _row(2, "refund trap now", "negative"),
        _row(3, "refund trap now", "negative"),
        _row(4, "refund trap now", "negative"),
        _row(5, "refund trap now uniqueonce", "negative"),
    ]
    rows.extend(_row(10 + index, "good useful", "positive") for index in range(5))
    result = analyse_keywords(
        rows,
        app_name="Nebula",
        reference_date=datetime(2026, 9, 29, tzinfo=UTC),
    )
    listed = {
        item["phrase"] for table in (result["common"], result["distinctive"]) for item in table
    }
    assert "uniqueonce" not in listed
    assert "refund" not in listed
    assert "refund trap now" in listed


def test_fewer_than_five_negative_reviews_is_explicitly_insufficient() -> None:
    rows = [_row(index, "bad refund", "negative") for index in range(4)]
    rows.extend(_row(10 + index, "great useful", "positive") for index in range(6))
    result = analyse_keywords(
        rows,
        app_name="Nebula",
        reference_date=datetime(2026, 9, 29, tzinfo=UTC),
    )
    assert result["status"] == "insufficient_negative_signal"
    assert result["common"] == []
    assert result["distinctive"] == []


def test_committed_snapshot_never_displays_only_blocked_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "appstore_review_analysis.text._language_identifier",
        lambda: _EnglishIdentifier(),
    )
    collection = FixtureProvider(ROOT / "data/fixtures").sample(1459969523, "us", 100, 42)
    analysis, _rows_out = analyse_collection(
        collection,
        request_payload={
            "app": "1459969523",
            "country": "us",
            "sample_size": 100,
            "seed": 42,
            "provider": "fixture",
            "analyze": True,
        },
        sentiment=FakeSentiment(),
        embedder=FakeEmbedder(),
        analyze=True,
        request_deadline_s=90,
    )
    payload = analysis.model_dump(mode="json")
    blocked = app_display_tokens(str(payload["app"]["name"]))
    for table_name in ("common", "distinctive"):
        for item in payload["keywords"][table_name]:
            assert displayable_phrase(item["phrase"], blocked), item["phrase"]
