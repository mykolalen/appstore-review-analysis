from datetime import UTC, datetime

from appstore_review_analysis.domain import Review
from appstore_review_analysis.text import analysis_text, lexical_text, preprocess_review


class _EnglishIdentifier:
    def classify(self, _text: str) -> tuple[str, float]:
        return "en", 0.99


def _review(*, title: str, body: str) -> Review:
    return Review(
        source_review_id="r1",
        rank=0,
        country="us",
        title=title,
        body=body,
        rating=4,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        is_edited=False,
        vote_count=0,
        vote_sum=0,
        has_developer_response=False,
        developer_response_id=None,
        developer_response_date=None,
        provider="fixture",
    )


def test_lexical_text_folds_apostrophes_before_contractions() -> None:
    assert lexical_text("I don’t like it and can't cancel; it’s odd") == (
        "i do not like it and cannot cancel its odd"
    )


def test_analysis_text_dedupes_title_and_avoids_double_punctuation() -> None:
    assert analysis_text("Same title", "Same title")[0] == "Same title"
    assert analysis_text("Love it!", "The readings are useful.")[0] == (
        "Love it! The readings are useful."
    )


def test_preprocessing_flags_edge_cases(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        "appstore_review_analysis.text._language_identifier",
        lambda: _EnglishIdentifier(),
    )
    emoji = preprocess_review(_review(title="😀", body=""))
    assert "emoji_only" in emoji.flags
    assert "title_only" in emoji.flags

    long_review = preprocess_review(_review(title="Long", body="word " * 3001))
    assert "long_text" in long_review.flags
    assert long_review.analysable is True


def test_title_is_a_duplicate_only_at_a_word_boundary() -> None:
    assert analysis_text("Great", "Great app")[1] is True
    assert analysis_text("Scam", "Scammers took my money")[1] is False
    assert analysis_text("Scam", "Scammers took my money")[0].startswith("Scam. Scammers")
    assert analysis_text("No", "Nothing works")[1] is False


def test_blank_review_is_not_analysable(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        "appstore_review_analysis.text._language_identifier", lambda: _EnglishIdentifier()
    )
    processed = preprocess_review(_review(title="  ", body=""))

    assert processed.analysable is False
    assert "no_text" in processed.flags
    assert "non_english" not in processed.flags
