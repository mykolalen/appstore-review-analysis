"""JSON and CSV export helpers shared by CLI and API routes."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from appstore_review_analysis.domain import AnalysedReview, CollectionResult, Review

_DANGEROUS_CSV_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n", "＝", "＋", "－", "＠")


def write_collection_json(result: CollectionResult, path: Path) -> None:
    """Write a collection with stable UTF-8 JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(result.model_dump(mode="json"), handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def escape_csv_text(value: str) -> str:
    """Neutralise spreadsheet-formula prefixes in free-text cells."""

    return f"\t{value}" if value.startswith(_DANGEROUS_CSV_PREFIXES) else value


def review_export_row(review: Review) -> dict[str, Any]:
    """Return only privacy-allowlisted or derived review fields."""

    return {
        "source_review_id": review.source_review_id,
        "rank": review.rank,
        "country": review.country,
        "title": escape_csv_text(review.title),
        "body": escape_csv_text(review.body),
        "rating": review.rating,
        "created_at": review.created_at.isoformat() if review.created_at else "",
        "is_edited": review.is_edited,
        "vote_count": review.vote_count,
        "vote_sum": review.vote_sum,
        "has_developer_response": review.has_developer_response,
        "developer_response_id": review.developer_response_id or "",
        "developer_response_date": (
            review.developer_response_date.isoformat() if review.developer_response_date else ""
        ),
        "app_version": review.app_version or "",
        "provider": review.provider,
        "source_flags": json.dumps(review.source_flags, ensure_ascii=False),
    }


def analysed_review_json_row(review: AnalysedReview) -> dict[str, Any]:
    """JSON-safe allowlisted review row including derived analysis fields."""

    return {
        "source_review_id": review.source_review_id,
        "rank": review.rank,
        "country": review.country,
        "title": review.title,
        "body": review.body,
        "rating": review.rating,
        "created_at": review.created_at.isoformat() if review.created_at else None,
        "is_edited": review.is_edited,
        "vote_count": review.vote_count,
        "vote_sum": review.vote_sum,
        "has_developer_response": review.has_developer_response,
        "developer_response_id": review.developer_response_id,
        "developer_response_date": (
            review.developer_response_date.isoformat() if review.developer_response_date else None
        ),
        "app_version": review.app_version,
        "provider": review.provider,
        "source_flags": list(review.source_flags),
        "flags": list(review.flags),
        "language": review.language,
        "sentiment_label": review.sentiment_label,
        "class_scores": dict(review.class_scores),
        "truncated": review.truncated,
    }


def analysed_review_csv_row(review: AnalysedReview) -> dict[str, Any]:
    """CSV row for stored reviews, with spreadsheet-injection-safe free text."""

    row = analysed_review_json_row(review)
    row["title"] = escape_csv_text(review.title)
    row["body"] = escape_csv_text(review.body)
    row["source_flags"] = json.dumps(review.source_flags, ensure_ascii=False)
    row["flags"] = json.dumps(review.flags, ensure_ascii=False)
    row["class_scores"] = json.dumps(review.class_scores, ensure_ascii=False, sort_keys=True)
    return row


def write_reviews_csv(
    reviews: list[Review],
    path: Path,
    *,
    excel: bool = False,
    delimiter: str = ",",
) -> None:
    """Write RFC-4180-style CSV; Excel mode adds only a UTF-8 BOM."""

    rows = [review_export_row(review) for review in reviews]
    fieldnames = list(rows[0]) if rows else _collection_fieldnames()
    _write_csv_rows(rows, fieldnames, path, excel=excel, delimiter=delimiter)


def render_analysed_reviews_csv(
    reviews: list[AnalysedReview],
    *,
    excel: bool = False,
    delimiter: str = ",",
) -> bytes:
    """Render analysed review rows to UTF-8 CSV bytes for API download."""

    _validate_delimiter(delimiter)
    rows = [analysed_review_csv_row(review) for review in reviews]
    fieldnames = list(rows[0]) if rows else _analysed_fieldnames()
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=fieldnames,
        delimiter=delimiter,
        lineterminator="\r\n",
        quoting=csv.QUOTE_MINIMAL,
    )
    writer.writeheader()
    writer.writerows(rows)
    raw = buffer.getvalue().encode("utf-8")
    return b"\xef\xbb\xbf" + raw if excel else raw


def _write_csv_rows(
    rows: list[dict[str, Any]],
    fieldnames: list[str],
    path: Path,
    *,
    excel: bool,
    delimiter: str,
) -> None:
    _validate_delimiter(delimiter)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoding = "utf-8-sig" if excel else "utf-8"
    with path.open("w", encoding=encoding, newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            delimiter=delimiter,
            lineterminator="\r\n",
            quoting=csv.QUOTE_MINIMAL,
        )
        writer.writeheader()
        writer.writerows(rows)


def _validate_delimiter(delimiter: str) -> None:
    if delimiter not in {",", ";"}:
        raise ValueError("delimiter must be ',' or ';'")


def _collection_fieldnames() -> list[str]:
    return [
        "source_review_id",
        "rank",
        "country",
        "title",
        "body",
        "rating",
        "created_at",
        "is_edited",
        "vote_count",
        "vote_sum",
        "has_developer_response",
        "developer_response_id",
        "developer_response_date",
        "app_version",
        "provider",
        "source_flags",
    ]


def _analysed_fieldnames() -> list[str]:
    return [
        "source_review_id",
        "rank",
        "country",
        "title",
        "body",
        "rating",
        "created_at",
        "is_edited",
        "vote_count",
        "vote_sum",
        "has_developer_response",
        "developer_response_id",
        "developer_response_date",
        "app_version",
        "provider",
        "source_flags",
        "flags",
        "language",
        "sentiment_label",
        "class_scores",
        "truncated",
    ]
