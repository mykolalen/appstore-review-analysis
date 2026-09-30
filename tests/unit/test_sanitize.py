from appstore_review_analysis.collection.itunes import (
    SANITISED_DEVELOPER_RESPONSE_KEYS,
    SANITISED_ROW_KEYS,
    sanitize_row,
)


def test_sanitize_row_enforces_privacy_allowlist() -> None:
    raw = {
        "userReviewId": "abc",
        "title": "Title",
        "body": "Body",
        "rating": 2,
        "date": "2026-09-28",
        "isEdited": False,
        "voteCount": 1,
        "voteSum": 1,
        "name": "DO NOT KEEP",
        "viewUsersUserReviewsUrl": "https://example.invalid/userProfileId=secret",
        "voteUrl": "https://example.invalid/vote",
        "developerResponse": {
            "id": "dr-1",
            "modified": "2026-09-29",
            "body": "DO NOT KEEP",
        },
    }

    clean = sanitize_row(raw)
    assert set(clean) <= SANITISED_ROW_KEYS
    assert "name" not in clean
    assert "viewUsersUserReviewsUrl" not in clean
    assert "voteUrl" not in clean
    assert set(clean["developerResponse"]) <= SANITISED_DEVELOPER_RESPONSE_KEYS
    assert "body" not in clean["developerResponse"]
