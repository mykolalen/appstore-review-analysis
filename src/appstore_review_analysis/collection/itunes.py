"""Keyless Apple iTunes collection provider used by the take-home demo.

The endpoints are undocumented and are intentionally isolated behind the provider interface.
"""

from __future__ import annotations

import math
import random
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from appstore_review_analysis.collection.http import JsonHttpClient, JsonResponseResult
from appstore_review_analysis.collection.sampling import draw_replacement_rank, sample_ranks
from appstore_review_analysis.collection.storefronts import normalise_country, storefront_header
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
POPULATION_URL = "https://itunes.apple.com/{country}/customer-reviews/id{app_id}"
ROWS_URL = "https://itunes.apple.com/WebObjects/MZStore.woa/wa/userReviewsRow"
ITUNES_USER_AGENT = "iTunes/12.12 (Windows; Microsoft Windows 10 x64) AppleWebKit/7613.2.7.1.1"
MAX_DISCOVERED_DEPTH = 300_001

# D16: this is the only Apple review payload shape permitted past sanitisation.
SANITISED_ROW_KEYS = {
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
SANITISED_DEVELOPER_RESPONSE_KEYS = {"id", "modified"}


@dataclass
class _RequestStats:
    requests_made: int = 0
    retries: int = 0

    def add(self, result: JsonResponseResult) -> None:
        self.requests_made += result.requests_made
        self.retries += result.retries


@dataclass(frozen=True)
class _FetchOutcome:
    rank: int
    review: Review | None
    invalid: bool
    empty: bool
    requests_made: int
    retries: int


_LOCKS_GUARD = threading.Lock()
_COLLECTION_LOCKS: dict[tuple[int, str], threading.Lock] = {}


def _collection_lock(app_id: int, country: str) -> threading.Lock:
    key = (app_id, country)
    with _LOCKS_GUARD:
        return _COLLECTION_LOCKS.setdefault(key, threading.Lock())


def sanitize_row(row: Any) -> dict[str, Any]:
    """Apply the D16 privacy allowlist before any downstream code sees an Apple row."""

    if not isinstance(row, dict):
        return {}
    out = {key: row.get(key) for key in SANITISED_ROW_KEYS if key in row}
    developer_response = row.get("developerResponse")
    if isinstance(developer_response, dict):
        out["developerResponse"] = {
            key: developer_response.get(key)
            for key in SANITISED_DEVELOPER_RESPONSE_KEYS
            if key in developer_response
        }
    else:
        out.pop("developerResponse", None)
    return out


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    raw = str(value).strip()
    if not raw:
        return None
    candidates = [raw, raw.replace("Z", "+00:00")]
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _review_from_row(
    row: dict[str, Any],
    *,
    rank: int | None,
    country: str,
) -> tuple[Review | None, bool]:
    """Convert one already-sanitised Apple row; bool marks an invalid required field."""

    review_id = row.get("userReviewId")
    rating = _optional_int(row.get("rating"))
    if review_id in (None, "") or rating is None or not 1 <= rating <= 5:
        return None, True

    title_value = row.get("title")
    body_value = row.get("body")
    title = "" if title_value is None else str(title_value).strip()
    body = "" if body_value is None else str(body_value).strip()

    flags: list[str] = []
    if not title:
        flags.append("missing_or_blank_title")
    if not body:
        flags.append("missing_or_blank_body")

    created_at = _parse_datetime(row.get("date"))
    if created_at is None:
        flags.append("missing_or_unparseable_date")

    developer_response = row.get("developerResponse")
    response_id: str | None = None
    response_date: datetime | None = None
    if isinstance(developer_response, dict) and developer_response:
        if developer_response.get("id") not in (None, ""):
            response_id = str(developer_response["id"])
        response_date = _parse_datetime(developer_response.get("modified"))

    return (
        Review(
            source_review_id=str(review_id),
            rank=rank,
            country=country,
            title=title,
            body=body,
            rating=rating,
            created_at=created_at,
            is_edited=_optional_bool(row.get("isEdited")),
            vote_count=_optional_int(row.get("voteCount")),
            vote_sum=_optional_int(row.get("voteSum")),
            has_developer_response=isinstance(developer_response, dict)
            and bool(developer_response),
            developer_response_id=response_id,
            developer_response_date=response_date,
            app_version=None,
            provider="itunes",
            source_flags=flags,
        ),
        False,
    )


class ITunesProvider:
    """Apple iTunes provider with exact population and rank access."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.Client | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
        now: Callable[[], datetime] | None = None,
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
        self._lookup_cache: dict[tuple[int, str], tuple[float, AppInfo]] = {}
        self._population_payload_cache: dict[tuple[int, str], tuple[float, dict[str, Any]]] = {}
        self._now = now or (lambda: datetime.now(UTC))

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> ITunesProvider:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _headers(self, country: str) -> dict[str, str]:
        return {
            "User-Agent": ITUNES_USER_AGENT,
            "X-Apple-Store-Front": storefront_header(country),
        }

    def app_info(self, app_id: int, country: str) -> AppInfo:
        stats = _RequestStats()
        return self._app_info(app_id, normalise_country(country), stats)

    def _app_info(self, app_id: int, country: str, stats: _RequestStats) -> AppInfo:
        cache_key = (app_id, country)
        cached = self._lookup_cache.get(cache_key)
        now = time.monotonic()
        if cached is not None and cached[0] > now:
            return cached[1].model_copy(deep=True)
        result = self.http.get_json(
            LOOKUP_URL,
            params={"id": app_id, "country": country},
            timeout=self.settings.collection_timeout_s,
            lookup_400_is_invalid=True,
        )
        stats.add(result)
        payload = result.data
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise _protocol("Apple lookup response has an unexpected schema.")
        if int(payload.get("resultCount", len(payload["results"]))) == 0 or not payload["results"]:
            raise AppError(
                status_code=404,
                code="APP_NOT_FOUND",
                message="App was not found in the requested storefront.",
                details={"app_id": app_id, "country": country},
            )
        item = payload["results"][0]
        if not isinstance(item, dict):
            raise _protocol("Apple lookup item has an unexpected schema.")
        app_info = AppInfo(
            app_id=app_id,
            name=str(item.get("trackName") or app_id),
            country=country,
            average_user_rating=_optional_float(item.get("averageUserRating")),
            user_rating_count=_optional_int(item.get("userRatingCount")),
            current_version_release_date=_parse_datetime(item.get("currentVersionReleaseDate")),
        )
        self._lookup_cache[cache_key] = (now + 900.0, app_info.model_copy(deep=True))
        return app_info

    def population(self, app_id: int, country: str) -> PopulationInfo:
        country = normalise_country(country)
        stats = _RequestStats()
        app = self._app_info(app_id, country, stats)
        return self._population(app_id, country, app, stats)

    def _population(
        self,
        app_id: int,
        country: str,
        app: AppInfo,
        stats: _RequestStats,
    ) -> PopulationInfo:
        cache_key = (app_id, country)
        cached = self._population_payload_cache.get(cache_key)
        now = time.monotonic()
        if cached is not None and cached[0] > now:
            payload: Any = dict(cached[1])
        else:
            result = self.http.get_json(
                POPULATION_URL.format(country=country, app_id=app_id),
                params={"dataOnly": "true", "displayable-kind": 11},
                headers=self._headers(country),
                timeout=self.settings.collection_timeout_s,
            )
            stats.add(result)
            payload = result.data
            if isinstance(payload, dict):
                self._population_payload_cache[cache_key] = (now + 900.0, dict(payload))
        if not isinstance(payload, dict):
            raise _protocol("Apple population response has an unexpected schema.")
        try:
            total = int(payload["totalNumberOfReviews"])
            rating_count = int(payload["ratingCount"])
            histogram = [int(value) for value in payload["ratingCountList"]]
        except (KeyError, TypeError, ValueError) as exc:
            raise _protocol("Apple population response is missing required fields.") from exc
        if len(histogram) != 5 or any(value < 0 for value in histogram) or total < 0:
            raise _protocol("Apple population response contains invalid counts.")
        histogram_total = sum(histogram)
        store_mean = (
            sum((index + 1) * count for index, count in enumerate(histogram)) / histogram_total
            if histogram_total
            else None
        )
        if (
            store_mean is not None
            and app.average_user_rating is not None
            and abs(store_mean - app.average_user_rating) > 0.01
        ):
            raise _protocol(
                "Apple rating histogram is inconsistent with the lookup average.",
                details={
                    "histogram_mean": round(store_mean, 6),
                    "lookup_mean": app.average_user_rating,
                },
            )

        depth = self._discover_depth(app_id, country, total, stats)
        reachable = min(total, depth)
        first_date: datetime | None = None
        last_date: datetime | None = None
        if reachable:
            first = self._fetch_rank(app_id, country, 0)
            stats.requests_made += first.requests_made
            stats.retries += first.retries
            last = self._fetch_rank(app_id, country, reachable - 1)
            stats.requests_made += last.requests_made
            stats.retries += last.retries
            first_date = first.review.created_at if first.review else None
            last_date = last.review.created_at if last.review else None

        return PopulationInfo(
            total_written_reviews=total,
            rating_count=rating_count,
            rating_count_list=histogram,
            store_mean=store_mean,
            reachable=reachable,
            frame_first_date=first_date,
            frame_last_date=last_date,
        )

    def _discover_depth(
        self,
        app_id: int,
        country: str,
        total: int,
        stats: _RequestStats,
    ) -> int:
        if total <= MAX_DISCOVERED_DEPTH:
            return total
        probe = self._fetch_rank(app_id, country, MAX_DISCOVERED_DEPTH - 1)
        stats.requests_made += probe.requests_made
        stats.retries += probe.retries
        if probe.review is not None:
            return MAX_DISCOVERED_DEPTH

        low = 0
        high = MAX_DISCOVERED_DEPTH - 1
        while low < high:
            mid = (low + high + 1) // 2
            outcome = self._fetch_rank(app_id, country, mid)
            stats.requests_made += outcome.requests_made
            stats.retries += outcome.retries
            if outcome.review is not None:
                low = mid
            else:
                high = mid - 1
        first = self._fetch_rank(app_id, country, low)
        stats.requests_made += first.requests_made
        stats.retries += first.retries
        return low + 1 if first.review is not None else 0

    def sample(
        self,
        app_id: int,
        country: str,
        sample_size: int,
        seed: int,
        *,
        window_days: int | None = None,
    ) -> CollectionResult:
        country = normalise_country(country)
        lock = _collection_lock(app_id, country)
        if not lock.acquire(timeout=self.settings.collection_lock_wait_s):
            raise AppError(
                status_code=409,
                code="COLLECTION_IN_PROGRESS",
                message="Another collection for this app and storefront is already running.",
                retry_after=max(1, math.ceil(self.settings.collection_lock_wait_s)),
            )
        try:
            if window_days is None:
                return self._sample_locked(app_id, country, sample_size, seed)
            return self._sample_locked(app_id, country, sample_size, seed, window_days=window_days)
        finally:
            lock.release()

    def _sample_locked(
        self,
        app_id: int,
        country: str,
        sample_size: int,
        seed: int,
        *,
        window_days: int | None = None,
    ) -> CollectionResult:
        started = time.monotonic()
        stats = _RequestStats()
        app = self._app_info(app_id, country, stats)
        population = self._population(app_id, country, app, stats)
        requested = sample_size
        frame_reachable = population.reachable
        frame_last_date = population.frame_last_date
        frame_description = (
            f"newest {population.reachable:,} of {population.total_written_reviews:,}"
        )
        if window_days is not None:
            if window_days < 1:
                raise AppError(
                    status_code=422,
                    code="INVALID_INPUT",
                    message="window_days must be at least 1.",
                    details={"window_days": window_days},
                )
            frame_reachable, frame_last_date = self._window_reachable(
                app_id,
                country,
                population,
                window_days=window_days,
                stats=stats,
            )
            frame_description = (
                f"newest {frame_reachable:,} reviews within the last {window_days} days "
                f"of {population.total_written_reviews:,} written reviews"
            )

        target = min(sample_size, frame_reachable)
        rng = random.Random(seed)
        initial_ranks = sample_ranks(frame_reachable, target, rng)
        used_ranks = set(initial_ranks)

        accepted: list[Review] = []
        accepted_ids: set[str] = set()
        rows_invalid = 0
        replacements: list[dict[str, int | str]] = []
        pending = list(initial_ranks)
        deadline_hit = False

        while pending and len(accepted) < target:
            if time.monotonic() - started >= self.settings.collection_deadline_s:
                deadline_hit = True
                break
            outcomes = self._fetch_wave(app_id, country, pending)
            failed: list[tuple[int, str]] = []
            for outcome in sorted(outcomes, key=lambda item: item.rank):
                stats.requests_made += outcome.requests_made
                stats.retries += outcome.retries
                if outcome.invalid:
                    rows_invalid += 1
                    failed.append((outcome.rank, "invalid_row"))
                    continue
                if outcome.empty or outcome.review is None:
                    failed.append((outcome.rank, "empty_row"))
                    continue
                if outcome.review.source_review_id in accepted_ids:
                    failed.append((outcome.rank, "duplicate_review"))
                    continue
                accepted_ids.add(outcome.review.source_review_id)
                accepted.append(outcome.review)

            pending = []
            for failed_rank, reason in sorted(failed):
                if len(replacements) >= self.settings.collection_max_replacements:
                    break
                replacement = draw_replacement_rank(frame_reachable, used_ranks, rng)
                if replacement is None:
                    break
                used_ranks.add(replacement)
                replacements.append(
                    {
                        "for_rank": failed_rank,
                        "replacement_rank": replacement,
                        "reason": reason,
                    }
                )
                pending.append(replacement)

        accepted.sort(key=lambda review: review.rank if review.rank is not None else -1)
        warnings: list[str] = []
        if frame_reachable == 0:
            if window_days is None:
                warnings.append(f"no written reviews in storefront {country}")
            else:
                warnings.append(
                    f"No written reviews fall within the requested {window_days}-day window."
                )
        if requested > frame_reachable:
            warnings.append(
                f"Only {frame_reachable} reviews are reachable in the selected sampling frame."
            )
        if deadline_hit:
            warnings.append(
                "Collection deadline reached before the requested sample was completed."
            )
        if len(accepted) < target and not deadline_hit:
            warnings.append(
                "The requested sample could not be completed after bounded replacements."
            )

        actual = len(accepted)
        metadata = SamplingMetadata(
            provider="itunes",
            method="uniform_random_rank",
            storefront=country,
            population_total=population.total_written_reviews,
            reachable=frame_reachable,
            frame_description=frame_description,
            window_days=window_days,
            frame_first_date=population.frame_first_date if frame_reachable else None,
            frame_last_date=frame_last_date if frame_reachable else None,
            requested=requested,
            actual=actual,
            sample_complete=actual == requested,
            sampling_fraction=(actual / frame_reachable if frame_reachable else None),
            seed=seed,
            ranks=initial_ranks,
            replacements=replacements,
            rows_invalid=rows_invalid,
            collected_at=datetime.now(UTC),
            requests_made=stats.requests_made,
            retries=stats.retries,
            store_histogram=population.rating_count_list,
            store_mean=population.store_mean,
            store_rating_count=population.rating_count,
        )
        return CollectionResult(app=app, sampling=metadata, reviews=accepted, warnings=warnings)

    def _window_reachable(
        self,
        app_id: int,
        country: str,
        population: PopulationInfo,
        *,
        window_days: int,
        stats: _RequestStats,
    ) -> tuple[int, datetime | None]:
        """Return the prefix length whose review dates are within the requested UTC window."""

        if population.reachable == 0:
            return 0, None
        cutoff = self._now().astimezone(UTC) - timedelta(days=window_days)
        newest = population.frame_first_date
        oldest = population.frame_last_date
        if newest is None or oldest is None:
            raise _protocol("Apple review dates are required for window_days sampling.")
        if newest < cutoff:
            return 0, None
        if oldest >= cutoff:
            return population.reachable, oldest

        low = 0  # known to be inside the window
        high = population.reachable - 1  # known to be outside the window
        low_date = newest
        while low + 1 < high:
            mid = (low + high) // 2
            outcome = self._fetch_rank(app_id, country, mid)
            stats.requests_made += outcome.requests_made
            stats.retries += outcome.retries
            if outcome.review is None or outcome.review.created_at is None:
                raise _protocol("Apple review date is missing inside the window search.")
            if outcome.review.created_at >= cutoff:
                low = mid
                low_date = outcome.review.created_at
            else:
                high = mid
        return low + 1, low_date

    def _fetch_wave(self, app_id: int, country: str, ranks: list[int]) -> list[_FetchOutcome]:
        if not ranks:
            return []
        with ThreadPoolExecutor(max_workers=self.settings.collection_concurrency) as executor:
            futures = {
                executor.submit(self._fetch_rank, app_id, country, rank): rank for rank in ranks
            }
            return [future.result() for future in as_completed(futures)]

    def _fetch_rank(self, app_id: int, country: str, rank: int) -> _FetchOutcome:
        result = self.http.get_json(
            ROWS_URL,
            params={
                "id": app_id,
                "displayable-kind": 11,
                "startIndex": rank,
                "endIndex": rank + 1,
                "sort": 4,
            },
            headers=self._headers(country),
            timeout=self.settings.collection_timeout_s,
            retry_non_json=True,
        )
        payload = result.data
        if not isinstance(payload, dict) or "userReviewList" not in payload:
            raise _protocol("Apple review-row response is missing userReviewList.")
        rows = payload["userReviewList"]
        if not isinstance(rows, list):
            raise _protocol("Apple review-row response has an invalid userReviewList.")
        if not rows:
            return _FetchOutcome(
                rank=rank,
                review=None,
                invalid=False,
                empty=True,
                requests_made=result.requests_made,
                retries=result.retries,
            )
        sanitized = sanitize_row(rows[0])
        review, invalid = _review_from_row(sanitized, rank=rank, country=country)
        return _FetchOutcome(
            rank=rank,
            review=review,
            invalid=invalid,
            empty=False,
            requests_made=result.requests_made,
            retries=result.retries,
        )

    def fetch_window(
        self,
        app_id: int,
        country: str,
        start: int,
        end: int,
    ) -> tuple[list[Review], int, int]:
        """Fetch up to 1,000 rows for the one-off population walk."""

        country = normalise_country(country)
        if start < 0 or end <= start or end - start > 1000:
            raise ValueError("window must satisfy 0 <= start < end and contain at most 1000 rows")
        result = self.http.get_json(
            ROWS_URL,
            params={
                "id": app_id,
                "displayable-kind": 11,
                "startIndex": start,
                "endIndex": end,
                "sort": 4,
            },
            headers=self._headers(country),
            timeout=self.settings.collection_timeout_s,
            retry_non_json=True,
        )
        payload = result.data
        if not isinstance(payload, dict) or "userReviewList" not in payload:
            raise _protocol("Apple review-window response is missing userReviewList.")
        rows = payload["userReviewList"]
        if not isinstance(rows, list):
            raise _protocol("Apple review-window response has an invalid userReviewList.")
        reviews: list[Review] = []
        for offset, raw in enumerate(rows):
            sanitized = sanitize_row(raw)
            review, invalid = _review_from_row(
                sanitized,
                rank=start + offset,
                country=country,
            )
            if review is not None and not invalid:
                reviews.append(review)
        return reviews, result.requests_made, result.retries


def _optional_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    raw = str(value).strip().lower()
    if raw in {"true", "1", "yes"}:
        return True
    if raw in {"false", "0", "no"}:
        return False
    return None


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _protocol(message: str, details: dict[str, Any] | None = None) -> AppError:
    return AppError(
        status_code=502,
        code="UPSTREAM_PROTOCOL_ERROR",
        message=message,
        details=details,
    )
