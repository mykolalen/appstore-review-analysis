"""Deterministic areas-of-improvement built only from computed evidence."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from appstore_review_analysis.analysis.keywords import collapse_subsumed
from appstore_review_analysis.domain import AnalysedReview


def build_areas_of_improvement(
    *,
    themes: Mapping[str, Any],
    keywords: Mapping[str, Any],
    rows: list[AnalysedReview],
    issue_categories: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    """Merge transparent categories with supported semantic themes and phrase fallbacks."""

    by_id = {row.source_review_id: row for row in rows}
    category_payload = dict(issue_categories or {})
    category_areas = _category_areas(category_payload)
    theme_areas = _theme_areas(themes)
    if not category_areas and not theme_areas:
        theme_areas = _phrase_fallbacks(keywords, by_id)

    category_areas.sort(key=_area_sort_key)
    theme_areas.sort(key=_area_sort_key)
    areas = [*category_areas, *theme_areas]
    status = "ok" if areas else "insufficient_signal"
    return {
        "status": status,
        "issue_categories": category_payload,
        "areas_of_improvement": areas,
    }


def _category_areas(issue_categories: Mapping[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for raw in issue_categories.get("items", []):
        if not isinstance(raw, dict):
            continue
        label = str(raw.get("label") or "Issue category")
        count = int(raw.get("review_count", 0))
        total = int(raw.get("denominator", 0))
        share = raw.get("share")
        ci = raw.get("ci95")
        raw_recency = raw.get("recency")
        recency: dict[str, Any] = raw_recency if isinstance(raw_recency, dict) else {}
        evidence = [item for item in raw.get("evidence", []) if isinstance(item, dict)]
        evidence_ids = [
            str(item.get("review_id")) for item in evidence if item.get("review_id") is not None
        ]
        excerpt = str(evidence[0].get("excerpt", "")) if evidence else ""
        output.append(
            {
                "source": "issue_category",
                "theme_id": None,
                "category_id": raw.get("category_id"),
                "area": label,
                "complaint_reviews": {
                    "count": count,
                    "total": total,
                    "share": share,
                    "ci95": ci,
                },
                "mean_star_rating": raw.get("mean_star_rating"),
                "recency": {
                    "newest_date": recency.get("newest_date"),
                    "share_last_12_months": recency.get("share_last_12_months"),
                    "historical": bool(recency.get("historical", False)),
                },
                "evidence_review_ids": evidence_ids[:3],
                "description": raw.get("description"),
                "top_matched_phrases": raw.get("top_matched_phrases", []),
                "evidence": evidence,
                "suggested_investigation": raw.get("suggested_investigation"),
                "text": _area_text(label, count, total, share, ci, excerpt),
            }
        )
    return output


def _theme_areas(themes: Mapping[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for theme in list(themes.get("items", [])):
        if not isinstance(theme, dict):
            continue
        evidence_ids = [str(item) for item in theme.get("evidence_review_ids", [])]
        representatives = [
            item for item in theme.get("representative_units", []) if isinstance(item, dict)
        ]
        phrase_rows = [item for item in theme.get("top_phrases", []) if isinstance(item, dict)]
        collapsed_phrase_rows = collapse_subsumed(
            phrase_rows,
            prefer_longer=False,
            same_support_only=False,
        )
        phrase_names = [
            str(item.get("phrase")) for item in collapsed_phrase_rows if item.get("phrase")
        ]
        unit_count = int(theme.get("unit_count", 0))
        area = " / ".join(phrase_names[:2]) or f"Unlabelled complaint group ({unit_count} units)"
        share = theme.get("share_of_complaint_reviews")
        share_value = share.get("value") if isinstance(share, dict) else None
        ci = share.get("ci95") if isinstance(share, dict) else None
        count = int(theme.get("review_count", len(evidence_ids)))
        denominator = int(share.get("denominator", 0)) if isinstance(share, dict) else 0
        excerpt = str(representatives[0].get("excerpt", "")) if representatives else ""
        output.append(
            {
                "source": "theme",
                "theme_id": theme.get("theme_id"),
                "area": area,
                "complaint_reviews": {
                    "count": count,
                    "total": denominator,
                    "share": share_value,
                    "ci95": ci,
                },
                "mean_star_rating": theme.get("mean_star_rating"),
                "recency": {
                    "newest_date": theme.get("newest_date"),
                    "share_last_12_months": theme.get("share_last_12_months"),
                    "historical": bool(theme.get("historical", False)),
                },
                "evidence_review_ids": evidence_ids[:3],
                "text": _area_text(area, count, denominator, share_value, ci, excerpt),
            }
        )
    return output


def _phrase_fallbacks(
    keywords: Mapping[str, Any], by_id: dict[str, AnalysedReview]
) -> list[dict[str, Any]]:
    rows = keywords.get("distinctive") or keywords.get("common") or []
    output: list[dict[str, Any]] = []
    for phrase_row in list(rows)[:3]:
        if not isinstance(phrase_row, dict):
            continue
        phrase = str(phrase_row.get("phrase", "negative feedback"))
        evidence_id = phrase_row.get("example_review_id")
        review = by_id.get(str(evidence_id)) if evidence_id is not None else None
        excerpt = review.analysis_text if review is not None else ""
        negative = phrase_row.get("negative_reviews", {})
        if not isinstance(negative, dict):
            negative = {}
        count_raw = negative.get("count")
        if count_raw is None:
            count_raw = phrase_row.get("support_count", 0)
        count = int(count_raw) if isinstance(count_raw, (int, float, str)) else 0

        total_raw = negative.get("total")
        if total_raw is None:
            total_raw = keywords.get("n_negative", 0)
        total = int(total_raw) if isinstance(total_raw, (int, float, str)) else 0
        share = count / total if total else None
        output.append(
            {
                "source": "phrase",
                "theme_id": None,
                "area": phrase,
                "complaint_reviews": {
                    "count": count,
                    "total": total,
                    "share": share,
                    "ci95": None,
                },
                "mean_star_rating": None,
                "recency": {
                    "newest_date": phrase_row.get("newest_date"),
                    "share_last_12_months": phrase_row.get("share_last_12_months"),
                    "historical": phrase_row.get("share_last_12_months") == 0,
                },
                "evidence_review_ids": [evidence_id] if evidence_id else [],
                "text": _area_text(phrase, count, total, share, None, excerpt),
            }
        )
    return output


def _area_text(
    area: str,
    count: int,
    total: int,
    share: object,
    ci: object,
    excerpt: str,
) -> str:
    share_text = f"{float(share):.1%}" if isinstance(share, (int, float)) else "unknown share"
    ci_text = ""
    if (
        isinstance(ci, dict)
        and isinstance(ci.get("low"), (int, float))
        and isinstance(ci.get("high"), (int, float))
    ):
        ci_text = f", 95% CI {float(ci['low']):.1%}-{float(ci['high']):.1%}"
    example = f", for example '{excerpt}'" if excerpt else ""
    return (
        f"Investigate {area}: {count} of {total} complaint reviews "
        f"({share_text}{ci_text}){example}."
    )


def _area_sort_key(item: Mapping[str, Any]) -> tuple[bool, float, str]:
    recency = item.get("recency")
    historical = bool(recency.get("historical", False)) if isinstance(recency, dict) else True
    return (historical, -_share_value(item), str(item.get("area", "")))


def _share_value(item: Mapping[str, Any]) -> float:
    complaint = item.get("complaint_reviews")
    if not isinstance(complaint, dict):
        return 0.0
    value = complaint.get("share")
    return float(value) if isinstance(value, (int, float)) else 0.0
