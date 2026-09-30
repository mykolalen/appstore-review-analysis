from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch

from appstore_review_analysis.analysis import sentiment as sentiment_module
from appstore_review_analysis.analysis.sentiment import (
    MODEL_MAX_LENGTH,
    SENTIMENT_MODEL_ALLOW_PATTERNS,
    SENTIMENT_MODEL_ID,
    SENTIMENT_MODEL_REVISION,
    TransformerSentiment,
    download_sentiment_model,
)


class _FakeTokenizer:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def encode(
        self,
        text: str,
        *,
        add_special_tokens: bool,
        truncation: bool,
    ) -> list[int]:
        assert add_special_tokens is True
        assert truncation is False
        return list(range(len(text.split()) + 2))

    def __call__(self, texts: list[str], **kwargs: object) -> dict[str, torch.Tensor]:
        self.calls.append(dict(kwargs))
        width = min(max(len(text.split()) + 2 for text in texts), MODEL_MAX_LENGTH)
        return {"input_ids": torch.ones((len(texts), width), dtype=torch.long)}


class _FakeModel:
    def __init__(self) -> None:
        self.config = SimpleNamespace(id2label={0: "positive", 1: "negative", 2: "neutral"})
        self.eval_called = False

    def eval(self) -> None:
        self.eval_called = True

    def __call__(self, **encoded: torch.Tensor) -> SimpleNamespace:
        batch = int(encoded["input_ids"].shape[0])
        logits = torch.tensor([[0.2, 4.0, 0.1]] * batch, dtype=torch.float32)
        return SimpleNamespace(logits=logits)


def test_labels_come_from_permuted_id2label_and_argmax_matches() -> None:
    tokenizer = _FakeTokenizer()
    model = _FakeModel()
    analyzer = TransformerSentiment(tokenizer=tokenizer, model=model, batch_size=2)

    results = analyzer.predict(["a normal review", "word " * 600])

    assert model.eval_called is True
    assert [result.label for result in results] == ["negative", "negative"]
    assert max(results[0].class_scores, key=results[0].class_scores.get) == results[0].label
    assert results[0].truncated is False
    assert results[1].truncated is True
    assert tokenizer.calls
    assert all(call["truncation"] is True for call in tokenizer.calls)
    assert all(call["max_length"] == 512 for call in tokenizer.calls)


def test_invalid_model_labels_are_rejected() -> None:
    tokenizer = _FakeTokenizer()
    model = _FakeModel()
    model.config.id2label = {0: "LABEL_0", 1: "LABEL_1", 2: "LABEL_2"}

    with pytest.raises(ValueError, match="Unsupported sentiment label"):
        TransformerSentiment(tokenizer=tokenizer, model=model)


def test_download_models_uses_pinned_allowlist_and_writes_tokenizer_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, object]] = []

    def fake_snapshot_download(**kwargs: object) -> str:
        calls.append(dict(kwargs))
        return str(kwargs["local_dir"])

    monkeypatch.setattr(sentiment_module, "snapshot_download", fake_snapshot_download)

    first = download_sentiment_model(tmp_path)
    second = download_sentiment_model(tmp_path)

    assert first == second
    assert len(calls) == 2
    for call in calls:
        assert call["repo_id"] == SENTIMENT_MODEL_ID
        assert call["revision"] == SENTIMENT_MODEL_REVISION
        assert call["allow_patterns"] == SENTIMENT_MODEL_ALLOW_PATTERNS
        assert Path(str(call["local_dir"])) == first

    config = json.loads((first / "tokenizer_config.json").read_text(encoding="utf-8"))
    assert config == {"model_max_length": 512}
