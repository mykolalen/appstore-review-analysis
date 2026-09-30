"""Explicit RSS fallback over Apple's newest-review feed.

This provider is intentionally not an automatic fallback from the iTunes rank provider: switching
frames silently would make a seeded analysis irreproducible. The RSS frame is capped at the newest
500 reviews and is labelled as such in sampling metadata.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from appstore_review_analysis.collection.http import JsonHttpClient
from appstore_review_analysis.collection.sampling import sample_ranks
from appstore_review_analysis.collection.storefronts import normalise_country
from appstore_review_analysis.config import Settings
from appstore_review_analysis.domain import (
    AppInfo,
    CollectionResult,
    PopulationInfo,
    Review,
    SamplingMetadata,
)
from appstore_review_analysis.errors import AppError

LOOKUP_URL = "https://itunes.apple.com/lookup"
RSS_URL = (
    "https://itunes.apple.com/{country}/rss/customerreviews/"
    "page={page}/id={app_id}/sortBy=mostRecent/json"
)
RSS_USER_AGENT = "appstore-review-analysis/0.1 (explicit RSS fallback)"
RSS_MAX_PAGES = 10
RSS_PAGE_SIZE = 50


class RSSProvider:
    """Newest-500 RSS fallback provider with cache-busting empty-feed retries."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.Client | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
        nonce_factory: Callable[[], str] | None = None,
    ) -> None:
        self.settings = settings
        self._owns_client = client is None
        self.client = client or httpx.Client()
        self.http = JsonHttpClient(
            self.client,
            max_retries=settings.http_max_retries,
            sleeper=sleeper,
            jitter=jitter,
        )
        self._nonce_factory = nonce_factory or (lambda: str(time.time_ns()))

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> RSSProvider:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def app_info(self, app_id: int, country: str) -> AppInfo:
        country = normalise_country(country)
        result = self.http.get_json(
            LOOKUP_URL,
            params={"id": app_id, "country": country},
            headers={"User-Agent": RSS_USER_AGENT},
            timeout=self.settings.collection_timeout_s,
            lookup_400_is_invalid=True,
        )
        payload = result.data
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise _protocol("Apple lookup response has an unexpected schema.")
        results = payload["results"]
        if int(payload.get("resultCount", len(results))) == 0 or not results:
            raise AppError(
                status_code=404,
                code="APP_NOT_FOUND",
                message="App was not found in the requested storefront.",
                details={"app_id": app_id, "country": country},
            )
        item = results[0]
        if not isinstance(item, dict):
            raise _protocol("Apple lookup item has an unexpected schema.")
        return AppInfo(
            app_id=app_id,
            name=str(item.get("trackName") or app_id),
            country=country,
            average_user_rating=_optional_float(item.get("averageUserRating")),
            user_rating_count=_optional_int(item.get("userRatingCount")),
            current_version_release_date=_parse_datetime(item.get("currentVersionReleaseDate")),
        )

    def population(self, app_id: int, country: str) -> PopulationInfo:
        country = normalise_country(country)
        # Validate the app/storefront through Lookup before interpreting an empty RSS feed.
        # Otherwise an invalid app could be misreported as a transient RSS outage.
        self.app_info(app_id, country)
        reviews, _requests, _retries = self._fetch_frame(app_id, country)
        return _population_from_reviews(reviews)

    def sample(
        self,
        app_id: int,
        country: str,
        sample_size: int,
        seed: int,
    ) -> CollectionResult:
        country = normalise_country(country)
        app_result = self.http.get_json(
            LOOKUP_URL,
            params={"id": app_id, "country": country},
            headers={"User-Agent": RSS_USER_AGENT},
            timeout=self.settings.collection_timeout_s,
            lookup_400_is_invalid=True,
        )
        app = _app_from_lookup(app_id, country, app_result.data)
        reviews, rss_requests, rss_retries = self._fetch_frame(app_id, country)
        population = _population_from_reviews(reviews)
        target = min(sample_size, population.reachable)
        ranks = sample_ranks(population.reachable, target, random.Random(seed))
        selected = [reviews[rank] for rank in ranks]
        warnings = [
            "RSS fallback samples only the newest reviews exposed by Apple's RSS feed; "
            "the frame is capped at 500 and is not the full written-review population."
        ]
        if sample_size > population.reachable:
            warnings.append(
                f"Only {population.reachable} reviews are reachable in the RSS fallback frame."
            )
        metadata = SamplingMetadata(
            provider="rss",
            method="uniform_random_rss_frame",
            storefront=country,
            population_total=population.total_written_reviews,
            reachable=population.reachable,
            frame_description=(
                f"newest {population.reachable:,} RSS reviews (fallback frame; max 500)"
            ),
            frame_first_date=population.frame_first_date,
            frame_last_date=population.frame_last_date,
            requested=sample_size,
            actual=len(selected),
            sample_complete=len(selected) == sample_size,
            sampling_fraction=(
                len(selected) / population.reachable if population.reachable else None
            ),
            seed=seed,
            ranks=ranks,
            collected_at=datetime.now(UTC),
            requests_made=app_result.requests_made + rss_requests,
            retries=app_result.retries + rss_retries,
            store_histogram=[0, 0, 0, 0, 0],
            store_mean=None,
            store_rating_count=0,
        )
        return CollectionResult(app=app, sampling=metadata, reviews=selected, warnings=warnings)

    def _fetch_frame(self, app_id: int, country: str) -> tuple[list[Review], int, int]:
        reviews: list[Review] = []
        seen_ids: set[str] = set()
        requests_made = 0
        retries = 0
        for page in range(1, RSS_MAX_PAGES + 1):
            entries, page_requests, page_retries, feed_was_empty = self._fetch_page(
                app_id,
                country,
                page,
            )
            requests_made += page_requests
            retries += page_retries
            if feed_was_empty:
                raise AppError(
                    status_code=503,
                    code="UPSTREAM_UNAVAILABLE",
                    message="Apple RSS returned an empty feed after cache-busting retries.",
                    retry_after=30,
                )

            page_review_count = 0
            for entry in entries:
                if "im:rating" not in entry:
                    continue
                review = _review_from_entry(entry, rank=len(reviews), country=country)
                if review is None or review.source_review_id in seen_ids:
                    continue
                seen_ids.add(review.source_review_id)
                reviews.append(review)
                page_review_count += 1

            if len(entries) < RSS_PAGE_SIZE or page_review_count == 0:
                break
        return reviews, requests_made, retries

    def _fetch_page(
        self,
        app_id: int,
        country: str,
        page: int,
    ) -> tuple[list[dict[str, Any]], int, int, bool]:
        requests_made = 0
        retries = 0
        for empty_attempt in range(self.settings.rss_empty_retries + 1):
            result = self.http.get_json(
                RSS_URL.format(country=country, page=page, app_id=app_id),
                params={"_": self._nonce_factory()},
                headers={"User-Agent": RSS_USER_AGENT},
                timeout=self.settings.collection_timeout_s,
            )
            requests_made += result.requests_made
            retries += result.retries
            entries = _entries_from_payload(result.data)
            if entries:
                retries += empty_attempt
                return entries, requests_made, retries, False
        retries += self.settings.rss_empty_retries
        return [], requests_made, retries, True


def _entries_from_payload(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise _protocol("Apple RSS response has an unexpected schema.")
    feed = payload.get("feed")
    if not isinstance(feed, dict):
        raise _protocol("Apple RSS response is missing feed.")
    raw_entries = feed.get("entry")
    if raw_entries is None:
        return []
    if isinstance(raw_entries, dict):
        return [raw_entries]
    if not isinstance(raw_entries, list):
        raise _protocol("Apple RSS entry has an unexpected schema.")
    return [entry for entry in raw_entries if isinstance(entry, dict)]


def _review_from_entry(entry: dict[str, Any], *, rank: int, country: str) -> Review | None:
    review_id = _label(entry.get("id"))
    rating = _optional_int(_label(entry.get("im:rating")))
    if not review_id or rating is None or not 1 <= rating <= 5:
        return None
    return Review(
        source_review_id=review_id,
        rank=rank,
        country=country,
        title=_label(entry.get("title")) or "",
        body=_label(entry.get("content")) or "",
        rating=rating,
        created_at=_parse_datetime(_label(entry.get("updated"))),
        is_edited=None,
        vote_count=_optional_int(_label(entry.get("im:voteCount"))),
        vote_sum=_optional_int(_label(entry.get("im:voteSum"))),
        has_developer_response=False,
        developer_response_id=None,
        developer_response_date=None,
        app_version=_label(entry.get("im:version")),
        provider="rss",
        source_flags=[],
    )


def _population_from_reviews(reviews: list[Review]) -> PopulationInfo:
    return PopulationInfo(
        total_written_reviews=len(reviews),
        rating_count=0,
        rating_count_list=[0, 0, 0, 0, 0],
        store_mean=None,
        reachable=len(reviews),
        frame_first_date=reviews[0].created_at if reviews else None,
        frame_last_date=reviews[-1].created_at if reviews else None,
    )


def _app_from_lookup(app_id: int, country: str, payload: Any) -> AppInfo:
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise _protocol("Apple lookup response has an unexpected schema.")
    results = payload["results"]
    if int(payload.get("resultCount", len(results))) == 0 or not results:
        raise AppError(
            status_code=404,
            code="APP_NOT_FOUND",
            message="App was not found in the requested storefront.",
            details={"app_id": app_id, "country": country},
        )
    item = results[0]
    if not isinstance(item, dict):
        raise _protocol("Apple lookup item has an unexpected schema.")
    return AppInfo(
        app_id=app_id,
        name=str(item.get("trackName") or app_id),
        country=country,
        average_user_rating=_optional_float(item.get("averageUserRating")),
        user_rating_count=_optional_int(item.get("userRatingCount")),
        current_version_release_date=_parse_datetime(item.get("currentVersionReleaseDate")),
    )


def _label(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("label")
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    raw = str(value).strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _protocol(message: str) -> AppError:
    return AppError(status_code=502, code="UPSTREAM_PROTOCOL_ERROR", message=message)
