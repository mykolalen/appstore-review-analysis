"""Privacy regression checks for committed JSON data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

PERSISTED_REVIEW_KEYS = {
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
}
RAW_REVIEW_KEYS = {
    "userReviewId",
    "title",
    "body",
    "rating",
    "date",
    "isEdited",
    "voteCount",
    "voteSum",
    "developerResponse",
}
FORBIDDEN_TEXT = ("userProfileId", "viewUsersUserReviewsUrl")


def test_committed_review_data_uses_personal_data_allowlist() -> None:
    root = Path(__file__).resolve().parents[2]
    roots = [root / "tests/fixtures", root / "data/fixtures", root / "reports", root / "evaluation"]
    checked_json = 0
    checked_text = 0
    for directory in roots:
        if not directory.exists():
            continue
        for path in sorted(item for item in directory.rglob("*") if item.is_file()):
            if path.suffix.lower() not in {".csv", ".json", ".jsonl", ".md", ".txt"}:
                continue
            text = path.read_text(encoding="utf-8")
            for forbidden in FORBIDDEN_TEXT:
                assert forbidden not in text, f"{forbidden} leaked into {path}"
            checked_text += 1
            if path.suffix.lower() != ".json":
                continue
            payload = json.loads(text)
            _check_payload(payload, path)
            checked_json += 1
    if checked_text == 0 or checked_json == 0:
        pytest.skip("No committed data files found.")


def _check_payload(value: Any, path: Path) -> None:
    if isinstance(value, dict):
        reviews = value.get("reviews")
        if isinstance(reviews, list):
            for review in reviews:
                if isinstance(review, dict):
                    unknown = set(review) - PERSISTED_REVIEW_KEYS
                    assert not unknown, f"Unexpected persisted review keys {unknown} in {path}"
                    assert "name" not in review
        raw_rows = value.get("userReviewList")
        if isinstance(raw_rows, list):
            for review in raw_rows:
                if isinstance(review, dict):
                    unknown = set(review) - RAW_REVIEW_KEYS
                    assert not unknown, f"Unexpected raw review keys {unknown} in {path}"
                    assert "name" not in review
        for child in value.values():
            _check_payload(child, path)
    elif isinstance(value, list):
        for child in value:
            _check_payload(child, path)
