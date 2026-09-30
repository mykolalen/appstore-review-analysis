"""Sentiment protocol, test double and pinned offline transformer implementation."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal, Protocol, cast

import torch
from huggingface_hub import snapshot_download
from pydantic import BaseModel

from appstore_review_analysis.hf_download import suppress_public_download_auth_warning

SentimentLabel = Literal["negative", "neutral", "positive"]
SENTIMENT_MODEL_ID = "cardiffnlp/twitter-roberta-base-sentiment-latest"
SENTIMENT_MODEL_REVISION = "3216a57f2a0d9c45a2e6c20157c20c49fb4bf9c7"  # pragma: allowlist secret
SENTIMENT_MODEL_DIRNAME = "cardiffnlp--twitter-roberta-base-sentiment-latest"
SENTIMENT_MODEL_ALLOW_PATTERNS = [
    "config.json",
    "vocab.json",
    "merges.txt",
    "special_tokens_map.json",
    "pytorch_model.bin",
]
MODEL_MAX_LENGTH = 512
DEFAULT_BATCH_SIZE = 32
MODEL_INFERENCE_LOCK = threading.Lock()


class SentimentResult(BaseModel):
    """One text sentiment prediction."""

    label: SentimentLabel
    class_scores: dict[str, float]
    truncated: bool = False
    model_id: str
    model_revision: str


class SentimentAnalyzer(Protocol):
    """Interface shared by fake and real transformer implementations."""

    def predict(self, texts: list[str]) -> list[SentimentResult]: ...


class FakeSentiment:
    """Small deterministic fake for unit/API tests only."""

    model_id = "fake-sentiment"
    model_revision = "test-v1"
    max_words = 512

    _negative = frozenset(
        {
            "awful",
            "bad",
            "broken",
            "charged",
            "crash",
            "crashed",
            "hate",
            "refund",
            "scam",
            "terrible",
            "worst",
        }
    )
    _positive = frozenset({"amazing", "excellent", "good", "great", "love", "perfect"})

    def predict(self, texts: list[str]) -> list[SentimentResult]:
        results: list[SentimentResult] = []
        for text in texts:
            tokens = {token.strip(".,!?;:\"'()[]{}").lower() for token in text.split()}
            if tokens & self._negative:
                label: SentimentLabel = "negative"
                scores = {"negative": 0.90, "neutral": 0.08, "positive": 0.02}
            elif tokens & self._positive:
                label = "positive"
                scores = {"negative": 0.02, "neutral": 0.08, "positive": 0.90}
            else:
                label = "neutral"
                scores = {"negative": 0.05, "neutral": 0.90, "positive": 0.05}
            results.append(
                SentimentResult(
                    label=label,
                    class_scores=scores,
                    truncated=len(text.split()) > self.max_words,
                    model_id=self.model_id,
                    model_revision=self.model_revision,
                )
            )
        return results


class TransformerSentiment:
    """Pinned CardiffNLP RoBERTa classifier loaded entirely from local files."""

    def __init__(
        self,
        *,
        tokenizer: Any,
        model: Any,
        model_id: str = SENTIMENT_MODEL_ID,
        model_revision: str = SENTIMENT_MODEL_REVISION,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        self.tokenizer = tokenizer
        self.model = model
        self.model_id = model_id
        self.model_revision = model_revision
        self.batch_size = batch_size
        self.model.eval()
        self._labels = _labels_from_config(self.model.config.id2label)

    @classmethod
    def load(cls, models_dir: Path) -> TransformerSentiment:
        """Load the pinned model from MODELS_DIR without network access."""

        _configure_offline_environment()
        model_path = sentiment_model_path(models_dir)
        if not model_path.exists():
            raise FileNotFoundError(
                f"Sentiment model is not downloaded at {model_path}. Run 'reviews download-models'."
            )

        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
        with _suppress_expected_transformers_load_report():
            model = AutoModelForSequenceClassification.from_pretrained(
                model_path, local_files_only=True
            )
        torch.set_num_threads(max(1, os.cpu_count() or 1))
        return cls(tokenizer=tokenizer, model=model)

    def predict(self, texts: list[str]) -> list[SentimentResult]:
        if not texts:
            return []

        token_lengths = [
            len(self.tokenizer.encode(text, add_special_tokens=True, truncation=False))
            for text in texts
        ]
        order = sorted(range(len(texts)), key=lambda index: token_lengths[index])
        results: list[SentimentResult | None] = [None] * len(texts)

        for start in range(0, len(order), self.batch_size):
            batch_indexes = order[start : start + self.batch_size]
            batch_texts = [texts[index] for index in batch_indexes]
            encoded = self.tokenizer(
                batch_texts,
                padding=True,
                truncation=True,
                max_length=MODEL_MAX_LENGTH,
                return_tensors="pt",
            )
            with MODEL_INFERENCE_LOCK, torch.inference_mode():
                outputs = self.model(**encoded)
                probabilities = torch.softmax(outputs.logits, dim=-1).detach().cpu()

            for row_index, original_index in enumerate(batch_indexes):
                class_scores = {
                    label: float(probabilities[row_index, class_index].item())
                    for class_index, label in self._labels.items()
                }
                predicted_index = int(torch.argmax(probabilities[row_index]).item())
                predicted_label = self._labels[predicted_index]
                results[original_index] = SentimentResult(
                    label=predicted_label,
                    class_scores=class_scores,
                    truncated=token_lengths[original_index] > MODEL_MAX_LENGTH,
                    model_id=self.model_id,
                    model_revision=self.model_revision,
                )

        return [cast(SentimentResult, item) for item in results]


@contextmanager
def _suppress_expected_transformers_load_report() -> Iterator[None]:
    """Hide the checkpoint's known-unused RoBERTa pooler-key load report only.

    The pinned checkpoint includes base-model pooler weights that
    ``RobertaForSequenceClassification`` does not consume. Transformers reports
    those keys as unexpected even though the classifier weights load correctly.
    Temporarily lowering Transformers logging keeps CLI output clean while model
    loading exceptions still propagate normally. The previous verbosity is always
    restored so unrelated warnings remain visible.
    """

    from transformers.utils import logging as transformers_logging

    previous_verbosity = transformers_logging.get_verbosity()
    try:
        cast(Any, transformers_logging.set_verbosity_error)()
        yield
    finally:
        transformers_logging.set_verbosity(previous_verbosity)


def sentiment_model_path(models_dir: Path) -> Path:
    """Return the deterministic local directory for the pinned sentiment model."""

    return models_dir / SENTIMENT_MODEL_DIRNAME


def download_sentiment_model(models_dir: Path) -> Path:
    """Download only the pinned files required for offline sentiment inference."""

    os.environ["DISABLE_SAFETENSORS_CONVERSION"] = "1"
    target = sentiment_model_path(models_dir)
    target.mkdir(parents=True, exist_ok=True)
    with suppress_public_download_auth_warning():
        snapshot_download(
            repo_id=SENTIMENT_MODEL_ID,
            revision=SENTIMENT_MODEL_REVISION,
            local_dir=target,
            allow_patterns=SENTIMENT_MODEL_ALLOW_PATTERNS,
        )
    tokenizer_config = target / "tokenizer_config.json"
    tokenizer_config.write_text(
        json.dumps({"model_max_length": MODEL_MAX_LENGTH}, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def _configure_offline_environment() -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["DISABLE_SAFETENSORS_CONVERSION"] = "1"
    if os.name == "nt":
        os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"


def _labels_from_config(id2label: Any) -> dict[int, SentimentLabel]:
    labels: dict[int, SentimentLabel] = {}
    for raw_index, raw_label in dict(id2label).items():
        index = int(raw_index)
        normalised = str(raw_label).strip().lower()
        if normalised not in {"negative", "neutral", "positive"}:
            raise ValueError(f"Unsupported sentiment label in model config: {raw_label!r}")
        labels[index] = cast(SentimentLabel, normalised)
    if set(labels.values()) != {"negative", "neutral", "positive"}:
        raise ValueError(
            "Sentiment model config must expose negative, neutral and positive labels."
        )
    return labels
