"""Text normalisation and preprocessing shared by analysis stages."""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from appstore_review_analysis.domain import Review

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WHITESPACE_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"[^\W_]+", flags=re.UNICODE)
_APOSTROPHES = str.maketrans({"\u2019": "'", "\u2018": "'", "\u02bc": "'", "`": "'"})
_END_PUNCTUATION = (".", "!", "?")
LANGUAGE_MIN_CHARS = 20
LANGUAGE_CONFIDENCE_FLOOR = 0.80
LONG_TEXT_CHARS = 2_000


@dataclass(frozen=True)
class ProcessedText:
    """Derived text fields and flags for one review."""

    analysis_text: str
    lexical_text: str
    language: str
    analysable: bool
    flags: list[str]


def normalise_text(value: str) -> str:
    """Apply conservative normalisation while preserving punctuation and emoji."""

    text = unicodedata.normalize("NFKC", html.unescape(value))
    text = _CONTROL_RE.sub("", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def analysis_text(title: str, body: str) -> tuple[str, bool]:
    """Join title/body without duplicating repeated titles or punctuation."""

    clean_title = normalise_text(title)
    clean_body = normalise_text(body)
    duplicate = bool(
        clean_title
        and clean_body
        and (
            clean_title.casefold() == clean_body.casefold()
            or clean_body.casefold().startswith(clean_title.casefold())
        )
    )
    if duplicate:
        return clean_body, True
    if not clean_title:
        return clean_body, False
    if not clean_body:
        return clean_title, False
    separator = " " if _title_has_terminal_mark(clean_title) else ". "
    return f"{clean_title}{separator}{clean_body}", False


def _title_has_terminal_mark(title: str) -> bool:
    if title.endswith(_END_PUNCTUATION):
        return True
    if not title:
        return False
    last = title[-1]
    category = unicodedata.category(last)
    return category in {"So", "Sk"}


def lexical_text(value: str) -> str:
    """Create lexical form for later n-gram extraction, preserving negation."""

    text = normalise_text(value).lower().translate(_APOSTROPHES)
    text = re.sub(r"\bcan't\b", "cannot", text)
    text = re.sub(r"\bwon't\b", "will not", text)
    text = re.sub(r"n't\b", " not", text)
    text = re.sub(r"'re\b", " are", text)
    text = re.sub(r"'ve\b", " have", text)
    text = re.sub(r"'ll\b", " will", text)
    text = re.sub(r"'m\b", " am", text)
    text = re.sub(r"'d\b", " would", text)
    text = re.sub(r"(?<=\w)'(?=\w)", "", text)
    return " ".join(_TOKEN_RE.findall(text))


@lru_cache(maxsize=1)
def _language_identifier() -> Any:
    """Load py3langid lazily; dependency is declared in the project environment."""

    from py3langid.langid import MODEL_FILE, LanguageIdentifier  # type: ignore[import-untyped]

    return LanguageIdentifier.from_model_file(
        MODEL_FILE,
        norm_probs=True,
        min_confidence=LANGUAGE_CONFIDENCE_FLOOR,
    )


def detect_language(value: str) -> tuple[str, float | None]:
    """Return language and normalised score; short text deliberately abstains."""

    if len(value.strip()) < LANGUAGE_MIN_CHARS:
        return "und", None
    language, score = _language_identifier().classify(value)
    return str(language), float(score)


def preprocess_review(review: Review) -> ProcessedText:
    """Derive analysis/lexical text, language gate and preprocessing flags."""

    joined, duplicate = analysis_text(review.title, review.body)
    lexical = lexical_text(joined)
    language, _score = detect_language(joined)

    flags = list(review.source_flags)
    if not review.body.strip():
        flags.append("empty_body")
        if review.title.strip():
            flags.append("title_only")
    if joined and not any(character.isalnum() for character in joined):
        flags.append("emoji_only")
    if len(joined) < LANGUAGE_MIN_CHARS:
        flags.append("short")
    if len(joined) > LONG_TEXT_CHARS:
        flags.append("long_text")
    if duplicate:
        flags.append("duplicate_title_body")
    analysable = language in {"en", "und"}
    if not analysable:
        flags.append("non_english")

    return ProcessedText(
        analysis_text=joined,
        lexical_text=lexical,
        language=language,
        analysable=analysable,
        flags=_unique(flags),
    )


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
