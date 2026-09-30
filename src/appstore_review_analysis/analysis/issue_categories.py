"""Transparent, deterministic issue categories for complaint sentences."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from appstore_review_analysis.analysis.metrics import wilson_interval
from appstore_review_analysis.analysis.themes import ComplaintUnit
from appstore_review_analysis.text import lexical_text

ISSUE_CATEGORY_VERSION = "1"
_CATEGORY_CI_REASON = "sampling uncertainty only; lexicon precision not measured unless audited"
_NEGATION_TOKENS = {"not", "no", "never", "isnt", "wasnt"}
_NEGATION_GUARDED_TRIGGERS = {"scam", "fraud", "fake", "ad", "ads"}


@dataclass(frozen=True)
class IssueCategory:
    """One closed issue category and its author-written investigation hypothesis."""

    id: str
    label: str
    description: str
    phrases: tuple[str, ...]
    suggested_investigation: str


ISSUE_CATEGORIES: tuple[IssueCategory, ...] = (
    IssueCategory(
        id="billing_charges",
        label="Billing and unexpected charges",
        description=(
            "Complaints about billing events, debits, or charges the reviewer did not expect."
        ),
        phrases=(
            "bill",
            "billed",
            "billing",
            "charge",
            "charged",
            "charges",
            "charged again",
            "charged twice",
            "unexpected charge",
            "unexpected charges",
            "unauthorized charge",
            "unauthorized charges",
            "unauthorised charge",
            "unauthorised charges",
            "money taken",
            "took my money",
            "payment taken",
        ),
        suggested_investigation=(
            "Check whether purchase, renewal, and billing confirmations make the timing and amount "
            "of charges clear before money is taken."
        ),
    ),
    IssueCategory(
        id="subscription_cancellation",
        label="Subscription and cancellation",
        description=(
            "Complaints about subscriptions, renewals, or difficulty cancelling recurring access."
        ),
        phrases=(
            "subscription",
            "subscriptions",
            "subscribe",
            "subscribed",
            "renewal",
            "renewals",
            "renewed",
            "auto renew",
            "auto renewal",
            "cancel",
            "canceled",
            "cancelled",
            "canceling",
            "cancelling",
            "cancellation",
            "unsubscribe",
            "unsubscribed",
            "cannot cancel",
            "will not cancel",
        ),
        suggested_investigation=(
            "Check whether subscription terms, renewal status, and cancellation controls are easy "
            "to find and complete across supported purchase channels."
        ),
    ),
    IssueCategory(
        id="refunds",
        label="Refunds",
        description="Complaints about requesting, receiving, or being denied money back.",
        phrases=(
            "refund",
            "refunds",
            "refunded",
            "refund refused",
            "refund denied",
            "no refund",
            "never refunded",
            "did not get refund",
            "did not get a refund",
            "did not receive refund",
            "did not receive a refund",
            "money back",
        ),
        suggested_investigation=(
            "Check whether refund eligibility, request steps, ownership, and response timing are "
            "clear to customers."
        ),
    ),
    IssueCategory(
        id="pricing_paywall",
        label="Pricing and paywall",
        description=(
            "Complaints about price, paid access, trials, or features being blocked behind payment."
        ),
        phrases=(
            "paywall",
            "pay wall",
            "price",
            "prices",
            "pricing",
            "expensive",
            "too expensive",
            "overpriced",
            "cost",
            "costs",
            "pay",
            "paid",
            "have to pay",
            "need to pay",
            "pay to use",
            "pay to see",
            "not free",
            "free trial",
            "trial ended",
        ),
        suggested_investigation=(
            "Check whether pricing, trial conversion, and paid-feature boundaries are visible "
            "before users invest time in the product."
        ),
    ),
    IssueCategory(
        id="scam_trust",
        label="Scam and trust",
        description=(
            "Complaints that question the product's honesty, legitimacy, or representation."
        ),
        phrases=(
            "scam",
            "scammy",
            "fraud",
            "fraudulent",
            "fake",
            "deceptive",
            "misleading",
            "dishonest",
            "rip off",
            "ripoff",
            "money grab",
            "cash grab",
            "do not trust",
            "cannot trust",
        ),
        suggested_investigation=(
            "Check whether product claims, purchase flows, and delivered value match what users "
            "are shown before they commit."
        ),
    ),
    IssueCategory(
        id="content_accuracy_relevance",
        label="Content accuracy and relevance",
        description=(
            "Complaints that generated or delivered content is wrong, generic, vague, or "
            "irrelevant."
        ),
        phrases=(
            "not accurate",
            "inaccurate",
            "wrong",
            "incorrect",
            "not relevant",
            "irrelevant",
            "generic",
            "too generic",
            "vague",
            "repetitive",
            "not personalized",
            "not personalised",
            "does not match",
            "did not match",
            "made up",
            "copy paste",
        ),
        suggested_investigation=(
            "Check whether the content is specific to the user's inputs and whether unsupported or "
            "generic outputs can be detected before delivery."
        ),
    ),
    IssueCategory(
        id="service_responsiveness",
        label="Service responsiveness",
        description=(
            "Complaints about live agents, advisers, readers, sellers, or other human-delivered "
            "service."
        ),
        phrases=(
            "advisor",
            "advisors",
            "adviser",
            "advisers",
            "agent",
            "agents",
            "reader",
            "readers",
            "seller",
            "sellers",
            "no response",
            "did not respond",
            "does not respond",
            "not responding",
            "unresponsive",
            "kept waiting",
            "long wait",
        ),
        suggested_investigation=(
            "Check whether human-service availability, response-time expectations, and "
            "service-quality controls match what customers are promised."
        ),
    ),
    IssueCategory(
        id="customer_support",
        label="Customer support",
        description=(
            "Complaints about getting help from the product's support or customer-service function."
        ),
        phrases=(
            "customer support",
            "customer service",
            "support team",
            "support",
            "help desk",
            "helpdesk",
            "no help",
            "no reply",
            "never replied",
            "did not reply",
            "does not reply",
            "unhelpful",
            "no assistance",
        ),
        suggested_investigation=(
            "Check whether support requests reach an accountable owner and whether customers can "
            "see expected response and resolution paths."
        ),
    ),
    IssueCategory(
        id="ads_interruptions",
        label="Ads and interruptions",
        description=(
            "Complaints about advertising, pop-ups, or interruptions that disrupt product use."
        ),
        phrases=(
            "ad",
            "ads",
            "advert",
            "adverts",
            "advertisement",
            "advertisements",
            "popup",
            "popups",
            "pop up",
            "pop ups",
            "interrupt",
            "interrupts",
            "interrupted",
            "interruption",
            "interruptions",
        ),
        suggested_investigation=(
            "Check whether advertising frequency, placement, and interruption points are "
            "concentrated in flows users expect to complete without disruption."
        ),
    ),
    IssueCategory(
        id="bugs_stability",
        label="Bugs and stability",
        description=(
            "Complaints about crashes, errors, broken flows, loading failures, or poor performance."
        ),
        phrases=(
            "bug",
            "bugs",
            "crash",
            "crashed",
            "crashes",
            "crashing",
            "freeze",
            "freezes",
            "freezing",
            "frozen",
            "glitch",
            "glitches",
            "lag",
            "laggy",
            "slow",
            "broken",
            "stuck",
            "error",
            "errors",
            "not working",
            "does not work",
            "did not work",
            "will not load",
            "cannot load",
        ),
        suggested_investigation=(
            "Check whether the affected flows share a release, device, network condition, or "
            "backend dependency that can be reproduced from telemetry."
        ),
    ),
    IssueCategory(
        id="account_access",
        label="Account and access",
        description="Complaints about signing in, account access, verification, or credentials.",
        phrases=(
            "login",
            "log in",
            "sign in",
            "cannot login",
            "cannot log in",
            "cannot sign in",
            "locked out",
            "account locked",
            "cannot access",
            "password",
            "verification",
            "verify account",
        ),
        suggested_investigation=(
            "Check whether sign-in, account recovery, and verification failures are observable and "
            "recoverable without requiring manual support."
        ),
    ),
)


@dataclass
class _ReviewMatch:
    review_id: str
    rating: int
    created_at: datetime | None
    phrases: set[str]
    unit_matches: list[tuple[ComplaintUnit, tuple[str, ...]]]


def analyse_issue_categories(
    units: list[ComplaintUnit],
    *,
    reference_date: datetime,
) -> dict[str, object]:
    """Apply the closed issue lexicon to retained complaint units at review level."""

    complaint_review_ids = sorted({unit.review_id for unit in units})
    denominator = len(complaint_review_ids)
    by_category: dict[str, dict[str, _ReviewMatch]] = {
        category.id: {} for category in ISSUE_CATEGORIES
    }
    for unit in units:
        for category in ISSUE_CATEGORIES:
            phrases = match_category_phrases(unit.lexical_text, category)
            if not phrases:
                continue
            review_matches = by_category[category.id]
            existing = review_matches.get(unit.review_id)
            if existing is None:
                existing = _ReviewMatch(
                    review_id=unit.review_id,
                    rating=unit.rating,
                    created_at=unit.created_at,
                    phrases=set(),
                    unit_matches=[],
                )
                review_matches[unit.review_id] = existing
            existing.phrases.update(phrases)
            existing.unit_matches.append((unit, phrases))

    items: list[dict[str, object]] = []
    audit_rows: list[dict[str, str]] = []
    supported_review_ids: set[str] = set()
    for category in ISSUE_CATEGORIES:
        review_matches = by_category[category.id]
        if len(review_matches) < 2:
            continue
        record, rows = _category_record(
            category,
            review_matches,
            denominator=denominator,
            reference_date=reference_date,
        )
        supported_review_ids.update(review_matches)
        items.append(record)
        audit_rows.extend(rows)

    items.sort(
        key=lambda item: (
            bool(_dict(item.get("recency")).get("historical", False)),
            -_float(item.get("share")),
            str(item.get("label", "")),
        )
    )
    audit_rows.sort(key=lambda row: (row["category"], row["review_id"]))

    uncategorised_ids = [
        review_id for review_id in complaint_review_ids if review_id not in supported_review_ids
    ]
    unit_by_review: dict[str, list[ComplaintUnit]] = defaultdict(list)
    for unit in units:
        unit_by_review[unit.review_id].append(unit)
    uncategorised_evidence = _uncategorised_evidence(uncategorised_ids, unit_by_review)

    return {
        "status": "ok" if units else "no_complaint_units",
        "version": ISSUE_CATEGORY_VERSION,
        "denominator": denominator,
        "multi_label": True,
        "items": items,
        "not_categorised": {
            "review_count": len(uncategorised_ids),
            "denominator": denominator,
            "share": len(uncategorised_ids) / denominator if denominator else None,
            "review_ids": uncategorised_ids,
            "evidence": uncategorised_evidence,
        },
        "audit_rows": audit_rows,
    }


def match_category_phrases(value: str, category: IssueCategory) -> tuple[str, ...]:
    """Return category phrases that match ``value`` as contiguous whole tokens."""

    tokens = value.split()
    matches: list[str] = []
    for phrase in category.phrases:
        phrase_tokens = phrase.split()
        if _contains_phrase(tokens, phrase_tokens):
            matches.append(phrase)
    return tuple(matches)


def _contains_phrase(tokens: list[str], phrase_tokens: list[str]) -> bool:
    if not phrase_tokens or len(phrase_tokens) > len(tokens):
        return False
    width = len(phrase_tokens)
    for start in range(len(tokens) - width + 1):
        if tokens[start : start + width] != phrase_tokens:
            continue
        if _has_negated_guarded_trigger(tokens, phrase_tokens, start):
            continue
        return True
    return False


def _has_negated_guarded_trigger(tokens: list[str], phrase_tokens: list[str], start: int) -> bool:
    for offset, token in enumerate(phrase_tokens):
        if token not in _NEGATION_GUARDED_TRIGGERS:
            continue
        position = start + offset
        if any(token in _NEGATION_TOKENS for token in tokens[max(0, position - 2) : position]):
            return True
    return False


def _category_record(
    category: IssueCategory,
    review_matches: dict[str, _ReviewMatch],
    *,
    denominator: int,
    reference_date: datetime,
) -> tuple[dict[str, object], list[dict[str, str]]]:
    reviews = list(review_matches.values())
    support = len(reviews)
    interval = wilson_interval(support, denominator) if denominator else None
    recent_cutoff = reference_date - timedelta(days=365)
    recent = [
        review
        for review in reviews
        if review.created_at is not None and review.created_at >= recent_cutoff
    ]
    dated = [review.created_at for review in reviews if review.created_at is not None]
    newest = max(dated, default=None)
    phrase_counts = Counter(phrase for review in reviews for phrase in review.phrases)
    top_phrases = [
        {"phrase": phrase, "count": count}
        for phrase, count in sorted(
            phrase_counts.items(),
            key=lambda item: (-item[1], -len(item[0].split()), item[0]),
        )[:5]
    ]
    ranked_reviews = sorted(reviews, key=_evidence_review_key)
    evidence = [
        {
            "review_id": review.review_id,
            "excerpt": _truncate_excerpt(_best_unit_match(review)[0].text),
        }
        for review in ranked_reviews[:3]
    ]
    audit_rows: list[dict[str, str]] = []
    for review in sorted(reviews, key=lambda value: value.review_id):
        unit, phrases = _best_unit_match(review)
        matched_phrase = sorted(phrases, key=lambda phrase: (-len(phrase.split()), phrase))[0]
        audit_rows.append(
            {
                "category": category.id,
                "review_id": review.review_id,
                "matched_phrase": matched_phrase,
                "sentence": unit.text,
            }
        )

    return (
        {
            "category_id": category.id,
            "label": category.label,
            "description": category.description,
            "review_count": support,
            "denominator": denominator,
            "share": support / denominator if denominator else None,
            "ci95": (
                {
                    "low": interval[0],
                    "high": interval[1],
                    "reason": _CATEGORY_CI_REASON,
                }
                if interval is not None
                else {"low": None, "high": None, "reason": "no complaint reviews"}
            ),
            "mean_star_rating": (
                sum(review.rating for review in reviews) / support if support else None
            ),
            "recency": {
                "newest_date": newest.isoformat() if newest is not None else None,
                "share_last_12_months": len(recent) / support if support else None,
                "historical": not recent,
            },
            "top_matched_phrases": top_phrases,
            "evidence": evidence,
            "matched_review_ids": sorted(review_matches),
            "suggested_investigation": category.suggested_investigation,
        },
        audit_rows,
    )


def _evidence_review_key(review: _ReviewMatch) -> tuple[int, bool, float, str]:
    timestamp = review.created_at.timestamp() if review.created_at is not None else 0.0
    return (-len(review.phrases), review.created_at is None, -timestamp, review.review_id)


def _best_unit_match(review: _ReviewMatch) -> tuple[ComplaintUnit, tuple[str, ...]]:
    return sorted(
        review.unit_matches,
        key=lambda item: (-len(item[1]), -item[0].negative_score, item[0].unit_id),
    )[0]


def _uncategorised_evidence(
    review_ids: list[str], unit_by_review: dict[str, list[ComplaintUnit]]
) -> list[dict[str, str]]:
    rows: list[tuple[bool, float, str, ComplaintUnit]] = []
    for review_id in review_ids:
        units = unit_by_review.get(review_id, [])
        if not units:
            continue
        unit = sorted(units, key=lambda item: (-item.negative_score, item.unit_id))[0]
        timestamp = unit.created_at.timestamp() if unit.created_at is not None else 0.0
        rows.append((unit.created_at is None, -timestamp, review_id, unit))
    rows.sort(key=lambda item: (item[0], item[1], item[2]))
    return [
        {"review_id": review_id, "excerpt": _truncate_excerpt(unit.text)}
        for _undated, _timestamp, review_id, unit in rows[:3]
    ]


def _truncate_excerpt(value: str) -> str:
    clean = value.strip()
    return clean if len(clean) <= 300 else clean[:297].rstrip() + "..."


_APP_SPECIFIC_TOKENS = frozenset({"nebula", "astrology"})


def validate_category_table(
    categories: tuple[IssueCategory, ...] | None = None,
    *,
    forbidden_tokens: frozenset[str] = _APP_SPECIFIC_TOKENS,
) -> None:
    """Static guard that the shipped lexicon stays generic and well-formed.

    This checks the table itself, never the analysed app: a store name such as "Pay" or "X"
    must not stop an analysis just because it shares a token or substring with a phrase.
    """

    table = ISSUE_CATEGORIES if categories is None else categories
    ids = [category.id for category in table]
    if len(ids) != len(set(ids)):
        raise ValueError("issue category ids must be unique")
    for category in table:
        if not category.suggested_investigation.startswith("Check whether "):
            raise ValueError("issue category investigations must be phrased as hypotheses")
        for phrase in category.phrases:
            if phrase != phrase.lower() or lexical_text(phrase) != phrase:
                raise ValueError(f"issue category phrase is not lexical-normalised: {phrase!r}")
            if set(phrase.split()) & forbidden_tokens:
                raise ValueError("issue category table contains app-specific vocabulary")


validate_category_table()


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _float(value: object) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return 0.0
