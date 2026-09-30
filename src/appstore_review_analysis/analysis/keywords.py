"""Negative-review phrase tables: common support and Fightin' Words distinctiveness."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any

from appstore_review_analysis.domain import AnalysedReview
from appstore_review_analysis.text import lexical_text

DEFAULT_A0 = 100.0
MAX_PHRASES = 15

DISPLAY_NEGATIONS = frozenset({"not", "no", "never", "cannot", "without"})
DISPLAY_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "being",
        "but",
        "by",
        "about",
        "above",
        "after",
        "again",
        "against",
        "all",
        "am",
        "any",
        "before",
        "below",
        "between",
        "both",
        "can",
        "during",
        "each",
        "few",
        "for",
        "from",
        "further",
        "had",
        "has",
        "have",
        "he",
        "her",
        "hers",
        "him",
        "his",
        "i",
        "if",
        "into",
        "in",
        "is",
        "it",
        "its",
        "me",
        "more",
        "most",
        "my",
        "nor",
        "of",
        "off",
        "on",
        "once",
        "only",
        "or",
        "other",
        "our",
        "ours",
        "same",
        "she",
        "should",
        "so",
        "some",
        "such",
        "that",
        "the",
        "than",
        "their",
        "theirs",
        "them",
        "they",
        "this",
        "through",
        "too",
        "to",
        "under",
        "until",
        "up",
        "us",
        "very",
        "was",
        "what",
        "where",
        "while",
        "why",
        "we",
        "were",
        "will",
        "with",
        "within",
        "would",
        "you",
        "your",
        "yours",
    }
)
GENERIC_APP_TOKENS = frozenset(
    {
        "app",
        "apps",
        "application",
        "nebula",
        "astrology",
        "astrologer",
        "horoscope",
        "horoscopes",
        "zodiac",
        "spiritual",
        "guidance",
    }
)
DISPLAY_FILLERS = frozenset(
    {
        "also",
        "anything",
        "did",
        "do",
        "does",
        "even",
        "give",
        "gives",
        "gave",
        "get",
        "gets",
        "getting",
        "got",
        "just",
        "made",
        "make",
        "maybe",
        "now",
        "out",
        "really",
        "something",
        "still",
        "then",
        "thing",
        "things",
        "use",
        "used",
        "using",
        "want",
        "wanted",
        "when",
    }
)


def analyse_keywords(
    rows: list[AnalysedReview],
    *,
    app_name: str,
    reference_date: datetime,
    a0: float = DEFAULT_A0,
) -> dict[str, object]:
    """Build literal-common and contrastive phrase tables from model-negative reviews."""

    negative = [row for row in rows if row.analysable and row.sentiment_label == "negative"]
    rest = [row for row in rows if row.analysable and row.sentiment_label != "negative"]
    if len(negative) < 5:
        return {
            "status": "insufficient_negative_signal",
            "n_negative": len(negative),
            "n_rest": len(rest),
            "common": [],
            "distinctive": [],
            "reason": "fewer_than_5_negative_reviews",
            "a0": a0,
        }

    neg_presence, neg_examples = _document_presence(negative)
    rest_presence, _ = _document_presence(rest)
    blocked_app_tokens = app_display_tokens(app_name)
    min_support = 3 if len(negative) >= 30 else 2

    common_rows: list[dict[str, Any]] = []
    for phrase, support in neg_presence.items():
        if support < min_support or not displayable_phrase(phrase, blocked_app_tokens):
            continue
        common_rows.append(
            _row_metadata(
                phrase,
                support,
                len(negative),
                rest_presence.get(phrase, 0),
                len(rest),
                neg_examples[phrase],
                negative,
                reference_date,
            )
        )
    common_rows.sort(key=lambda row: (-int(row["support_count"]), str(row["phrase"])))
    common_rows = collapse_subsumed(common_rows, prefer_longer=True)[:MAX_PHRASES]

    distinctive_rows = _fightin_words_rows(
        neg_presence,
        rest_presence,
        negative,
        rest,
        neg_examples,
        reference_date=reference_date,
        blocked_app_tokens=blocked_app_tokens,
        min_support=min_support,
        a0=a0,
    )[:MAX_PHRASES]

    return {
        "status": "ok",
        "n_negative": len(negative),
        "n_rest": len(rest),
        "common": common_rows,
        "distinctive": distinctive_rows,
        "a0": a0,
        "note": "Fightin' Words z is used as a ranking score, not a significance test.",
    }


def distinctive_phrases_for_texts(
    group_texts: list[str],
    rest_texts: list[str],
    *,
    min_support: int = 2,
    a0: float = DEFAULT_A0,
    limit: int = 5,
    app_name: str = "",
) -> list[dict[str, object]]:
    """Return concise, displayable Fightin' Words phrases for a complaint group."""

    group = _text_presence(group_texts)
    rest = _text_presence(rest_texts)
    if not group:
        return []
    blocked_app_tokens = app_display_tokens(app_name)
    ranked = _fightin_words_core(group, rest, a0=a0)
    output: list[dict[str, Any]] = []
    for phrase, z in ranked:
        support = group.get(phrase, 0)
        if support < min_support or not displayable_phrase(phrase, blocked_app_tokens):
            continue
        output.append({"phrase": phrase, "support_count": support, "z": z})
    # Theme labels are intentionally concise: when two n-grams have the same support and one
    # contains the other, keep the shorter representative instead of emitting a redundant label.
    return collapse_subsumed(
        output,
        prefer_longer=False,
        same_support_only=False,
    )[:limit]


def app_display_tokens(app_name: str) -> set[str]:
    """Return tokens that must not qualify a phrase for display on their own."""

    return set(GENERIC_APP_TOKENS) | set(lexical_text(app_name).split())


def displayable_phrase(phrase: str, blocked_app_tokens: set[str] | None = None) -> bool:
    """Return True when a phrase contains at least one substantive content token."""

    blocked_app_tokens = blocked_app_tokens or set(GENERIC_APP_TOKENS)
    tokens = phrase.split()
    if not tokens:
        return False
    blocked = DISPLAY_STOPWORDS | DISPLAY_NEGATIONS | DISPLAY_FILLERS | blocked_app_tokens
    return any(_content_token(token, blocked) for token in tokens)


def _content_token(token: str, blocked: set[str] | frozenset[str]) -> bool:
    """Return True for a lexical content token rather than punctuation or a bare number."""

    return token not in blocked and any(character.isalpha() for character in token)


def collapse_subsumed(
    rows: list[dict[str, Any]],
    *,
    prefer_longer: bool,
    same_support_only: bool = True,
) -> list[dict[str, Any]]:
    """Collapse contiguous n-grams, optionally requiring identical document support."""

    output: list[dict[str, Any]] = []
    for row in rows:
        phrase = str(row["phrase"])
        support = int(row.get("support_count", 0))
        phrase_tokens = phrase.split()
        redundant = False
        for candidate in rows:
            other = str(candidate["phrase"])
            if other == phrase:
                continue
            if same_support_only and int(candidate.get("support_count", 0)) != support:
                continue
            other_tokens = other.split()
            if prefer_longer:
                if len(other_tokens) <= len(phrase_tokens):
                    continue
                if _contains_sequence(other_tokens, phrase_tokens):
                    redundant = True
                    break
            else:
                if len(other_tokens) >= len(phrase_tokens):
                    continue
                if _contains_sequence(phrase_tokens, other_tokens):
                    redundant = True
                    break
        if not redundant:
            output.append(row)
    return output


def _fightin_words_rows(
    neg_presence: Counter[str],
    rest_presence: Counter[str],
    negative: list[AnalysedReview],
    rest: list[AnalysedReview],
    neg_examples: dict[str, list[str]],
    *,
    reference_date: datetime,
    blocked_app_tokens: set[str],
    min_support: int,
    a0: float,
) -> list[dict[str, object]]:
    ranked = _fightin_words_core(neg_presence, rest_presence, a0=a0)
    rows_by_id = {row.source_review_id: row for row in negative}
    output: list[dict[str, Any]] = []
    for phrase, z in ranked:
        support = neg_presence.get(phrase, 0)
        if z <= 0 or support < min_support or not displayable_phrase(phrase, blocked_app_tokens):
            continue
        evidence_ids = neg_examples.get(phrase, [])
        evidence_rows = [rows_by_id[item] for item in evidence_ids if item in rows_by_id]
        newest = max(
            (row.created_at for row in evidence_rows if row.created_at is not None),
            default=None,
        )
        recent_count = sum(
            1
            for row in evidence_rows
            if row.created_at is not None and row.created_at >= reference_date - timedelta(days=365)
        )
        output.append(
            {
                "phrase": phrase,
                "negative_reviews": {
                    "count": support,
                    "total": len(negative),
                    "display": f"{support} of {len(negative)}",
                },
                "other_reviews": {
                    "count": rest_presence.get(phrase, 0),
                    "total": len(rest),
                    "display": f"{rest_presence.get(phrase, 0)} of {len(rest)}",
                },
                "support_count": support,
                "z": z,
                "z_ge_1_96": z >= 1.96,
                "example_review_id": evidence_ids[0] if evidence_ids else None,
                "newest_date": newest.isoformat() if newest is not None else None,
                "share_last_12_months": recent_count / support if support else None,
            }
        )
    output.sort(key=lambda row: (-float(row["z"]), -int(row["support_count"]), str(row["phrase"])))
    return collapse_subsumed(output, prefer_longer=True)


def _fightin_words_core(
    group: Counter[str], rest: Counter[str], *, a0: float
) -> list[tuple[str, float]]:
    vocabulary = sorted(set(group) | set(rest))
    n_group = sum(group.values())
    n_rest = sum(rest.values())
    n_total = n_group + n_rest
    if not vocabulary or n_group == 0 or n_total == 0:
        return []

    ranked: list[tuple[str, float]] = []
    for phrase in vocabulary:
        y_group = group.get(phrase, 0)
        y_rest = rest.get(phrase, 0)
        a_w = a0 * (y_group + y_rest) / n_total
        group_other = n_group + a0 - y_group - a_w
        rest_other = n_rest + a0 - y_rest - a_w
        if group_other <= 0 or rest_other <= 0 or y_group + a_w <= 0 or y_rest + a_w <= 0:
            continue
        delta = math.log((y_group + a_w) / group_other) - math.log((y_rest + a_w) / rest_other)
        variance = 1.0 / (y_group + a_w) + 1.0 / (y_rest + a_w)
        z = delta / math.sqrt(variance)
        ranked.append((phrase, z))
    ranked.sort(key=lambda item: (-item[1], item[0]))
    return ranked


def _document_presence(
    rows: Iterable[AnalysedReview],
) -> tuple[Counter[str], dict[str, list[str]]]:
    counts: Counter[str] = Counter()
    examples: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        phrases = _ngrams(row.lexical_text)
        counts.update(phrases)
        for phrase in phrases:
            examples[phrase].append(row.source_review_id)
    return counts, dict(examples)


def _text_presence(texts: Iterable[str]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for text in texts:
        counts.update(_ngrams(text))
    return counts


def _ngrams(value: str) -> set[str]:
    tokens = _tokens(value)
    output: set[str] = set()
    for size in (1, 2, 3):
        for start in range(0, len(tokens) - size + 1):
            output.add(" ".join(tokens[start : start + size]))
    return output


def _tokens(value: str) -> list[str]:
    return [token for token in value.lower().split() if token]


def _contains_sequence(longer: list[str], shorter: list[str]) -> bool:
    return any(
        longer[index : index + len(shorter)] == shorter
        for index in range(0, len(longer) - len(shorter) + 1)
    )


def _row_metadata(
    phrase: str,
    support: int,
    n_negative: int,
    rest_support: int,
    n_rest: int,
    evidence_ids: list[str],
    negative: list[AnalysedReview],
    reference_date: datetime,
) -> dict[str, Any]:
    by_id = {row.source_review_id: row for row in negative}
    evidence = [by_id[item] for item in evidence_ids if item in by_id]
    newest = max((row.created_at for row in evidence if row.created_at is not None), default=None)
    recent = sum(
        1
        for row in evidence
        if row.created_at is not None and row.created_at >= reference_date - timedelta(days=365)
    )
    return {
        "phrase": phrase,
        "support_count": support,
        "negative_reviews": {
            "count": support,
            "total": n_negative,
            "display": f"{support} of {n_negative}",
        },
        "other_reviews": {
            "count": rest_support,
            "total": n_rest,
            "display": f"{rest_support} of {n_rest}",
        },
        "example_review_id": evidence_ids[0] if evidence_ids else None,
        "newest_date": newest.isoformat() if newest is not None else None,
        "share_last_12_months": recent / support if support else None,
    }
