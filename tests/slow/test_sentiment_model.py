from __future__ import annotations

from pathlib import Path

import pytest

from appstore_review_analysis.analysis.sentiment import TransformerSentiment, sentiment_model_path

pytestmark = pytest.mark.slow


def test_real_sentiment_model_has_stable_labels_and_truncates_long_input() -> None:
    models_dir = Path("models")
    if not sentiment_model_path(models_dir).exists():
        pytest.skip("Run 'uv run reviews download-models' before the slow model test.")

    analyzer = TransformerSentiment.load(models_dir)
    results = analyzer.predict(
        [
            "I absolutely love this app. It is wonderful and works perfectly.",
            "This is the worst app I have ever used. It is terrible and broken.",
            "The app was updated yesterday and now has a settings page.",
            "great " * 3000,
        ]
    )

    assert [result.label for result in results[:3]] == ["positive", "negative", "neutral"]
    assert results[3].truncated is True
    for result in results:
        assert max(result.class_scores, key=result.class_scores.get) == result.label
