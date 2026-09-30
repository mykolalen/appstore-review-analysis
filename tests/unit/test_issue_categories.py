from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from appstore_review_analysis.analysis.issue_categories import (
    ISSUE_CATEGORIES,
    analyse_issue_categories,
    match_category_phrases,
    validate_category_table,
)
from appstore_review_analysis.analysis.pipeline import analyse_collection
from appstore_review_analysis.analysis.sentiment import FakeSentiment
from appstore_review_analysis.analysis.themes import ComplaintUnit, FakeEmbedder
from appstore_review_analysis.collection.fixture import FixtureProvider
from appstore_review_analysis.text import lexical_text

ROOT = Path(__file__).resolve().parents[2]

REFERENCE_DATE = datetime(2026, 9, 30, tzinfo=UTC)


def _category(category_id: str):  # type: ignore[no-untyped-def]
    return next(category for category in ISSUE_CATEGORIES if category.id == category_id)


def _unit(
    review_id: str,
    text: str,
    *,
    rating: int = 1,
    days_ago: int = 10,
    sentence: int = 0,
    negative_score: float = 0.9,
) -> ComplaintUnit:
    return ComplaintUnit(
        unit_id=f"{review_id}:s{sentence}",
        review_id=review_id,
        text=text,
        lexical_text=lexical_text(text),
        rating=rating,
        created_at=REFERENCE_DATE - timedelta(days=days_ago),
        negative_score=negative_score,
    )


@pytest.mark.parametrize(
    ("category_id", "positive", "negative"),
    [
        ("billing_charges", "I was charged twice.", "The discharge note was confusing."),
        ("subscription_cancellation", "I cannot cancel my subscription.", "The caption is wrong."),
        ("refunds", "I never received a refund.", "The fund page is slow."),
        ("pricing_paywall", "It is too expensive and I have to pay.", "The payment page loaded."),
        ("scam_trust", "This feels like a scam.", "The scan result was late."),
        (
            "content_accuracy_relevance",
            "The result is inaccurate and generic.",
            "The result is detailed.",
        ),
        ("service_responsiveness", "The advisor kept me waiting.", "The advice was useful."),
        (
            "customer_support",
            "Customer support did not reply.",
            "The tutorial answered my question.",
        ),
        ("ads_interruptions", "Too many ads and pop ups.", "The update was made yesterday."),
        ("bugs_stability", "It crashed and is not working.", "The workflow completed normally."),
        ("account_access", "I am locked out and cannot log in.", "The account page looks good."),
    ],
)
def test_each_category_has_planted_match_and_near_miss(
    category_id: str, positive: str, negative: str
) -> None:
    category = _category(category_id)
    assert match_category_phrases(lexical_text(positive), category)
    assert match_category_phrases(lexical_text(negative), category) == ()


def test_matching_is_whole_token_not_substring() -> None:
    ads = _category("ads_interruptions")
    pricing = _category("pricing_paywall")
    assert match_category_phrases(lexical_text("The ad was intrusive."), ads) == ("ad",)
    assert match_category_phrases(lexical_text("This was made carefully."), ads) == ()
    assert "pay" in match_category_phrases(lexical_text("I have to pay again."), pricing)
    assert "pay" not in match_category_phrases(lexical_text("The payment screen failed."), pricing)


def test_absence_of_ads_does_not_count_as_an_ads_complaint() -> None:
    ads = _category("ads_interruptions")
    assert match_category_phrases(lexical_text("There are no ads."), ads) == ()
    assert match_category_phrases(lexical_text("Except no ads."), ads) == ()
    assert "ads" in match_category_phrases(lexical_text("Too many ads."), ads)


def test_positive_words_do_not_trigger_categories_on_their_own() -> None:
    accuracy = _category("content_accuracy_relevance")
    bugs = _category("bugs_stability")
    trust = _category("scam_trust")
    assert match_category_phrases(lexical_text("Accurate and relevant."), accuracy) == ()
    assert match_category_phrases(lexical_text("Working perfectly."), bugs) == ()
    assert match_category_phrases(lexical_text("Real and trustworthy."), trust) == ()


def test_scam_fraud_fake_negation_guard() -> None:
    trust = _category("scam_trust")
    assert "scam" not in match_category_phrases(lexical_text("This is not a scam."), trust)
    assert "fraud" not in match_category_phrases(lexical_text("There is no fraud here."), trust)
    assert "fake" not in match_category_phrases(lexical_text("It was never fake."), trust)
    assert "scam" not in match_category_phrases(lexical_text("It wasn't a scam."), trust)
    assert "scam" in match_category_phrases(lexical_text("This is a scam."), trust)


def test_unicode_apostrophe_punctuation_and_emoji_are_normalised() -> None:
    refunds = _category("refunds")
    bugs = _category("bugs_stability")
    refund_matches = match_category_phrases(lexical_text("I didn’t get a refund 😡!!!"), refunds)
    assert "refund" in refund_matches
    assert "did not get a refund" in refund_matches
    assert "not working" in match_category_phrases(lexical_text("Still not working 🤦."), bugs)


def test_review_denominator_wilson_recency_and_multilabel() -> None:
    units = [
        _unit("r1", "I was charged twice and cannot cancel my subscription.", rating=1, days_ago=5),
        _unit("r2", "Unexpected charge after cancellation.", rating=2, days_ago=500),
        _unit("r3", "It crashes every time.", rating=1, days_ago=20),
        _unit("r4", "Horrible experience, that is all.", rating=1, days_ago=30),
    ]
    result = analyse_issue_categories(units, reference_date=REFERENCE_DATE)
    assert result["denominator"] == 4
    assert result["multi_label"] is True

    items = {item["category_id"]: item for item in result["items"]}  # type: ignore[index]
    billing = items["billing_charges"]
    assert billing["review_count"] == 2
    assert billing["denominator"] == 4
    assert billing["share"] == pytest.approx(0.5)
    assert billing["ci95"]["low"] == pytest.approx(  # type: ignore[index]
        0.15003898915214947
    )
    assert billing["ci95"]["high"] == pytest.approx(  # type: ignore[index]
        0.8499610108478506
    )
    assert billing["ci95"]["reason"] == (  # type: ignore[index]
        "sampling uncertainty only; lexicon precision not measured unless audited"
    )
    assert billing["mean_star_rating"] == pytest.approx(1.5)
    assert billing["recency"]["share_last_12_months"] == pytest.approx(0.5)  # type: ignore[index]
    assert billing["recency"]["historical"] is False  # type: ignore[index]
    assert billing["matched_review_ids"] == ["r1", "r2"]

    # r1 is counted once in each matching category, not once per matching phrase.
    subscription = items["subscription_cancellation"]
    assert subscription["review_count"] == 2
    assert subscription["matched_review_ids"] == ["r1", "r2"]

    not_categorised = result["not_categorised"]
    assert not_categorised["review_count"] == 2  # type: ignore[index]
    assert not_categorised["review_ids"] == ["r3", "r4"]  # type: ignore[index]


def test_category_order_is_recent_then_share_then_label() -> None:
    units = [
        _unit("r1", "refund and ads", days_ago=5),
        _unit("r2", "refund and ads", days_ago=6),
        _unit("r3", "charged", days_ago=500),
        _unit("r4", "charged", days_ago=600),
    ]
    result = analyse_issue_categories(units, reference_date=REFERENCE_DATE)
    assert [item["category_id"] for item in result["items"]] == [  # type: ignore[index]
        "ads_interruptions",
        "refunds",
        "billing_charges",
    ]


def test_historical_category_and_evidence_tiebreaks_are_deterministic() -> None:
    units = [
        _unit(
            "r3",
            "I was charged again, billing is wrong, and there was an unexpected charge.",
            days_ago=500,
            negative_score=0.91,
        ),
        _unit("r1", "I was charged.", days_ago=5, negative_score=0.95),
        _unit("r2", "I was charged.", days_ago=5, negative_score=0.94),
        _unit("r4", "This is inaccurate.", days_ago=600),
        _unit("r5", "This is not accurate.", days_ago=700),
    ]
    first = analyse_issue_categories(units, reference_date=REFERENCE_DATE)
    second = analyse_issue_categories(list(reversed(units)), reference_date=REFERENCE_DATE)
    assert first == second

    items = {item["category_id"]: item for item in first["items"]}  # type: ignore[index]
    billing = items["billing_charges"]
    evidence_ids = [row["review_id"] for row in billing["evidence"]]  # type: ignore[index]
    # More distinct matched phrases wins even when older; then newest, then review id.
    assert evidence_ids == ["r3", "r1", "r2"]

    accuracy = items["content_accuracy_relevance"]
    assert accuracy["recency"]["historical"] is True  # type: ignore[index]
    assert accuracy["recency"]["share_last_12_months"] == 0.0  # type: ignore[index]


def test_only_categories_with_two_reviews_are_shown_and_singletons_are_not_hidden() -> None:
    result = analyse_issue_categories(
        [_unit("r1", "This is a scam."), _unit("r2", "It crashes.")],
        reference_date=REFERENCE_DATE,
    )
    assert result["items"] == []
    assert result["not_categorised"]["review_count"] == 2  # type: ignore[index]
    assert result["not_categorised"]["review_ids"] == ["r1", "r2"]  # type: ignore[index]


def test_evidence_excerpt_is_matching_sentence_and_at_most_300_chars() -> None:
    long = "charged " + "x" * 400
    result = analyse_issue_categories(
        [_unit("r1", long), _unit("r2", "charged again")],
        reference_date=REFERENCE_DATE,
    )
    item = result["items"][0]  # type: ignore[index]
    assert item["category_id"] == "billing_charges"
    assert all(len(row["excerpt"]) <= 300 for row in item["evidence"])
    assert item["evidence"][0]["excerpt"].startswith("charged")


def test_payload_contains_no_non_finite_numbers() -> None:
    result = analyse_issue_categories(
        [_unit("r1", "refund please"), _unit("r2", "no refund")],
        reference_date=REFERENCE_DATE,
    )

    def visit(value: object) -> None:
        if isinstance(value, float):
            assert math.isfinite(value)
        elif isinstance(value, dict):
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(result)


def test_lexicon_is_generic_and_investigations_are_hypotheses() -> None:
    all_phrases = " ".join(phrase for category in ISSUE_CATEGORIES for phrase in category.phrases)
    assert "nebula" not in all_phrases
    assert "astrology" not in all_phrases
    assert len(ISSUE_CATEGORIES) in range(10, 13)
    assert all(
        category.suggested_investigation.startswith("Check whether ")
        for category in ISSUE_CATEGORIES
    )


def test_table_guard_rejects_app_specific_lexicon() -> None:
    # The shipped table is generic; guard against future app-specific edits.
    original = ISSUE_CATEGORIES[0]
    mutated = type(original)(
        id=original.id,
        label=original.label,
        description=original.description,
        phrases=(*original.phrases, "nebula readings"),
        suggested_investigation=original.suggested_investigation,
    )

    validate_category_table()
    with pytest.raises(ValueError, match="app-specific"):
        validate_category_table((mutated, *ISSUE_CATEGORIES[1:]))


class _EnglishIdentifier:
    def classify(self, _text: str) -> tuple[str, float]:
        return "en", 0.99


@pytest.mark.parametrize("app_name", ["X", "Pay", "Ads"])
def test_short_app_names_never_break_the_analysis(
    app_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Regression: the table guard used to test the app name as a substring of every phrase,
    # so an app called "X" (inside "expensive") crashed the whole analysis with a 500.
    monkeypatch.setattr(
        "appstore_review_analysis.text._language_identifier", lambda: _EnglishIdentifier()
    )
    collection = FixtureProvider(ROOT / "data/fixtures").sample(1459969523, "us", 100, 42)
    renamed = collection.model_copy(
        update={"app": collection.app.model_copy(update={"name": app_name})}
    )
    analysis, _rows = analyse_collection(
        renamed,
        request_payload={"app": "1459969523", "country": "us", "sample_size": 100, "seed": 42},
        sentiment=FakeSentiment(),
        embedder=FakeEmbedder(),
        analyze=True,
        request_deadline_s=90,
    )
    payload = analysis.model_dump(mode="json")
    assert payload["insights"]["issue_categories"]["status"] == "ok"
