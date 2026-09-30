from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from appstore_review_analysis.collection.rss import (
    RSS_USER_AGENT,
    RSSProvider,
    _entries_from_payload,
)
from appstore_review_analysis.config import Settings
from appstore_review_analysis.errors import AppError

ROOT = Path(__file__).resolve().parents[2]


def _fixture(name: str) -> dict[str, object]:
    return json.loads((ROOT / "tests" / "fixtures" / "rss" / name).read_text(encoding="utf-8"))


def _lookup() -> dict[str, object]:
    return {
        "resultCount": 1,
        "results": [
            {
                "trackName": "Nebula",
                "averageUserRating": 4.57,
                "userRatingCount": 170000,
                "currentVersionReleaseDate": "2026-09-20T00:00:00Z",
            }
        ],
    }


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "collection_timeout_s": 0.1,
        "http_max_retries": 0,
        "rss_empty_retries": 2,
    }
    values.update(overrides)
    return Settings(**values)


def test_cached_empty_feed_retries_with_new_nonce_and_filters_non_reviews() -> None:
    populated = _fixture("page_populated.json")
    empty = _fixture("page_empty.json")
    rss_calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/lookup":
            return httpx.Response(200, json=_lookup())
        if "/rss/customerreviews/" in request.url.path:
            rss_calls.append(request)
            return httpx.Response(200, json=empty if len(rss_calls) == 1 else populated)
        raise AssertionError(request.url)

    nonces = iter(["nonce-a", "nonce-b", "nonce-c"])
    provider = RSSProvider(
        _settings(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        nonce_factory=lambda: next(nonces),
    )
    result = provider.sample(1459969523, "us", 3, 42)

    assert len(rss_calls) == 2
    assert rss_calls[0].url.params["_"] == "nonce-a"
    assert rss_calls[1].url.params["_"] == "nonce-b"
    assert all(request.headers["User-Agent"] == RSS_USER_AGENT for request in rss_calls)
    assert {review.source_review_id for review in result.reviews} == {"rss-1", "rss-2", "rss-3"}
    assert all(review.provider == "rss" for review in result.reviews)
    assert result.sampling.provider == "rss"
    assert result.sampling.method == "uniform_random_rss_frame"
    assert result.sampling.reachable == 3
    assert result.sampling.requests_made == 3  # lookup + two RSS attempts
    assert result.sampling.retries == 1
    assert result.sampling.store_mean is None
    assert result.sampling.store_rating_count == 0
    assert any("capped at 500" in warning for warning in result.warnings)


def test_rss_dict_entry_is_coerced_to_one_item() -> None:
    entry = {
        "id": {"label": "rss-1"},
        "im:rating": {"label": "5"},
    }
    assert _entries_from_payload({"feed": {"entry": entry}}) == [entry]


def test_empty_first_page_after_retry_budget_is_upstream_unavailable() -> None:
    empty = _fixture("page_empty.json")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if request.url.path == "/lookup":
            return httpx.Response(200, json=_lookup())
        calls += 1
        return httpx.Response(200, json=empty)

    provider = RSSProvider(
        _settings(rss_empty_retries=2),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        nonce_factory=iter(["a", "b", "c"]).__next__,
    )
    with pytest.raises(AppError) as exc_info:
        provider.sample(1459969523, "us", 3, 42)

    assert calls == 3
    assert exc_info.value.status_code == 503
    assert exc_info.value.code == "UPSTREAM_UNAVAILABLE"
    assert exc_info.value.retry_after == 30


def test_population_checks_lookup_before_treating_empty_feed_as_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/lookup":
            return httpx.Response(200, json={"resultCount": 0, "results": []})
        raise AssertionError("RSS must not be fetched for an app that lookup cannot find")

    provider = RSSProvider(
        _settings(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(AppError) as exc_info:
        provider.population(999, "us")

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "APP_NOT_FOUND"
