from __future__ import annotations

import random
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from appstore_review_analysis.collection.fixture import FixtureProvider
from appstore_review_analysis.collection.itunes import (
    ITunesProvider,
    _review_from_row,
    sanitize_row,
)
from appstore_review_analysis.collection.sampling import sample_ranks
from appstore_review_analysis.config import Settings
from appstore_review_analysis.domain import AppInfo, CollectionResult, Review, SamplingMetadata
from appstore_review_analysis.errors import AppError

HISTOGRAM = [10443, 2084, 5688, 13375, 139189]
STORE_MEAN = sum((i + 1) * count for i, count in enumerate(HISTOGRAM)) / sum(HISTOGRAM)


def _settings(**overrides: object) -> Settings:
    base = {
        "collection_timeout_s": 0.1,
        "collection_deadline_s": 5.0,
        "collection_lock_wait_s": 0.01,
        "collection_concurrency": 4,
        "collection_max_replacements": 20,
        "http_max_retries": 1,
    }
    base.update(overrides)
    return Settings(**base)


def _lookup_payload() -> dict[str, object]:
    return {
        "resultCount": 1,
        "results": [
            {
                "trackName": "Nebula",
                "averageUserRating": STORE_MEAN,
                "userRatingCount": sum(HISTOGRAM),
                "currentVersionReleaseDate": "2026-09-20T00:00:00Z",
            }
        ],
    }


def _population_payload(total: int = 20, histogram: list[int] | None = None) -> dict[str, object]:
    values = histogram or HISTOGRAM
    return {
        "totalNumberOfReviews": total,
        "ratingCount": sum(values),
        "ratingCountList": values,
    }


def _row(rank: int, *, review_id: str | None = None, rating: int = 5) -> dict[str, object]:
    return {
        "userReviewId": review_id if review_id is not None else f"r-{rank}",
        "title": f"Title {rank}",
        "body": f"Body {rank}",
        "rating": rating,
        "date": f"2026-09-{(rank % 27) + 1:02d}",
        "isEdited": False,
        "voteCount": 0,
        "voteSum": 0,
        "name": "private-name",
        "viewUsersUserReviewsUrl": "https://example.invalid/userProfileId=private",
        "developerResponse": {
            "id": f"d-{rank}",
            "modified": "2026-09-29",
            "body": "private developer response body",
        },
    }


@pytest.mark.parametrize(
    "field",
    [
        "userReviewId",
        "rating",
        "title",
        "body",
        "date",
        "isEdited",
        "voteCount",
        "voteSum",
        "developerResponse",
    ],
)
def test_missing_review_fields_follow_required_and_optional_policy(field: str) -> None:
    row = _row(3)
    row.pop(field)
    review, invalid = _review_from_row(sanitize_row(row), rank=3, country="us")

    if field in {"userReviewId", "rating"}:
        assert invalid is True
        assert review is None
        return

    assert invalid is False
    assert review is not None
    if field == "title":
        assert review.title == ""
        assert "missing_or_blank_title" in review.source_flags
    elif field == "body":
        assert review.body == ""
        assert "missing_or_blank_body" in review.source_flags
    elif field == "date":
        assert review.created_at is None
        assert "missing_or_unparseable_date" in review.source_flags
    elif field == "isEdited":
        assert review.is_edited is None
    elif field == "voteCount":
        assert review.vote_count is None
    elif field == "voteSum":
        assert review.vote_sum is None
    elif field == "developerResponse":
        assert review.has_developer_response is False
        assert review.developer_response_id is None
        assert review.developer_response_date is None


def _router(
    *,
    total: int = 20,
    invalid_rank: int | None = None,
    duplicate_rank: int | None = None,
    delay_by_rank: bool = False,
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/lookup":
            return httpx.Response(200, json=_lookup_payload())
        if "/customer-reviews/" in path:
            return httpx.Response(200, json=_population_payload(total))
        if path.endswith("/userReviewsRow"):
            rank = int(request.url.params["startIndex"])
            if delay_by_rank:
                time.sleep((rank % 4) * 0.002)
            row = _row(rank)
            if rank == invalid_rank:
                row.pop("userReviewId", None)
            if rank == duplicate_rank:
                row["userReviewId"] = "r-0"
            return httpx.Response(200, json={"userReviewList": [row]})
        raise AssertionError(f"unexpected URL {request.url}")

    return httpx.MockTransport(handler)


def test_population_reads_histogram_one_star_first_and_checks_mean() -> None:
    client = httpx.Client(transport=_router())
    provider = ITunesProvider(_settings(), client=client, sleeper=lambda _: None)
    population = provider.population(1459969523, "us")

    assert population.rating_count_list == HISTOGRAM
    assert population.rating_count == 170779
    assert population.store_mean == pytest.approx(4.5738, abs=0.0001)
    assert population.total_written_reviews == 20
    assert population.reachable == 20


def test_histogram_mismatch_is_protocol_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/lookup":
            return httpx.Response(200, json=_lookup_payload())
        if "/customer-reviews/" in request.url.path:
            return httpx.Response(200, json=_population_payload(20, [20, 0, 0, 0, 0]))
        raise AssertionError(request.url)

    provider = ITunesProvider(
        _settings(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleeper=lambda _: None,
    )
    with pytest.raises(AppError) as exc_info:
        provider.population(1459969523, "us")
    assert exc_info.value.code == "UPSTREAM_PROTOCOL_ERROR"


def test_non_json_200_is_protocol_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/lookup":
            return httpx.Response(200, json=_lookup_payload())
        return httpx.Response(200, text="<html>Connecting to the iTunes Store</html>")

    provider = ITunesProvider(
        _settings(http_max_retries=0),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleeper=lambda _: None,
    )
    with pytest.raises(AppError) as exc_info:
        provider.population(1459969523, "us")
    assert exc_info.value.code == "UPSTREAM_PROTOCOL_ERROR"


def test_lookup_400_and_not_found_have_stable_codes() -> None:
    provider_400 = ITunesProvider(
        _settings(http_max_retries=0),
        client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(400, json={}))),
        sleeper=lambda _: None,
    )
    with pytest.raises(AppError) as exc_info:
        provider_400.app_info(1459969523, "us")
    assert exc_info.value.code == "INVALID_INPUT"

    provider_missing = ITunesProvider(
        _settings(),
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={"resultCount": 0, "results": []})
            )
        ),
        sleeper=lambda _: None,
    )
    with pytest.raises(AppError) as exc_info:
        provider_missing.app_info(1459969523, "us")
    assert exc_info.value.code == "APP_NOT_FOUND"


def test_429_honours_retry_after_then_returns_rate_limited() -> None:
    sleeps: list[float] = []
    provider = ITunesProvider(
        _settings(http_max_retries=1),
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(429, headers={"Retry-After": "2"}, json={})
            )
        ),
        sleeper=sleeps.append,
    )
    with pytest.raises(AppError) as exc_info:
        provider.app_info(1459969523, "us")
    assert exc_info.value.code == "UPSTREAM_RATE_LIMITED"
    assert exc_info.value.retry_after == 2
    assert sleeps == [2.0]


def test_timeout_is_mapped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("boom", request=request)

    provider = ITunesProvider(
        _settings(http_max_retries=0),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleeper=lambda _: None,
    )
    with pytest.raises(AppError) as exc_info:
        provider.app_info(1459969523, "us")
    assert exc_info.value.code == "UPSTREAM_TIMEOUT"


def test_missing_and_duplicate_rows_get_deterministic_replacements_despite_delays() -> None:
    population = 30
    seed = 42
    initial = sample_ranks(population, 10, random.Random(seed))
    invalid_rank = initial[2]
    duplicate_rank = initial[5]

    def run() -> CollectionResult:
        client = httpx.Client(
            transport=_router(
                total=population,
                invalid_rank=invalid_rank,
                duplicate_rank=duplicate_rank,
                delay_by_rank=True,
            )
        )
        provider = ITunesProvider(_settings(), client=client, sleeper=lambda _: None)
        return provider.sample(1459969523, "us", 10, seed)

    first = run()
    second = run()
    assert first.sampling.ranks == second.sampling.ranks == initial
    assert first.sampling.replacements == second.sampling.replacements
    assert first.sampling.rows_invalid == second.sampling.rows_invalid == 1
    assert len({review.source_review_id for review in first.reviews}) == first.sampling.actual
    assert first.sampling.actual == 10
    assert len(first.sampling.replacements) == 2


def test_zero_and_fewer_than_requested_are_valid_partial_results() -> None:
    zero = ITunesProvider(
        _settings(), client=httpx.Client(transport=_router(total=0)), sleeper=lambda _: None
    ).sample(1459969523, "us", 100, 42)
    assert zero.sampling.actual == 0
    assert not zero.sampling.sample_complete
    assert any("no written reviews" in warning for warning in zero.warnings)

    small = ITunesProvider(
        _settings(), client=httpx.Client(transport=_router(total=3)), sleeper=lambda _: None
    ).sample(1459969523, "us", 100, 42)
    assert small.sampling.actual == 3
    assert not small.sampling.sample_complete
    assert any("Only 3 reviews" in warning for warning in small.warnings)


def test_second_collection_for_same_app_country_is_rejected() -> None:
    entered = threading.Event()
    release = threading.Event()

    class BlockingProvider(ITunesProvider):
        def _sample_locked(
            self, app_id: int, country: str, sample_size: int, seed: int
        ) -> CollectionResult:
            entered.set()
            release.wait(timeout=2)
            return CollectionResult(
                app=AppInfo(app_id=app_id, name="x", country=country),
                sampling=SamplingMetadata(
                    provider="itunes",
                    method="uniform_random_rank",
                    storefront=country,
                    population_total=0,
                    reachable=0,
                    frame_description="newest 0 of 0",
                    frame_first_date=None,
                    frame_last_date=None,
                    requested=sample_size,
                    actual=0,
                    sample_complete=False,
                    sampling_fraction=None,
                    seed=seed,
                    ranks=[],
                    collected_at=datetime.now(UTC),
                ),
                reviews=[],
            )

    provider = BlockingProvider(_settings(collection_lock_wait_s=0.01), client=httpx.Client())
    errors: list[Exception] = []

    def first_call() -> None:
        try:
            provider.sample(1, "us", 1, 1)
        except Exception as exc:  # pragma: no cover - diagnostic path
            errors.append(exc)

    thread = threading.Thread(target=first_call)
    thread.start()
    assert entered.wait(timeout=1)
    with pytest.raises(AppError) as exc_info:
        provider.sample(1, "us", 1, 1)
    assert exc_info.value.code == "COLLECTION_IN_PROGRESS"
    release.set()
    thread.join(timeout=2)
    assert not errors


def test_fixture_provider_replays_without_network(tmp_path: Path) -> None:
    reviews = [
        Review(
            source_review_id=f"r-{i}",
            rank=i,
            country="us",
            title="t",
            body="b",
            rating=5,
            created_at=None,
            is_edited=None,
            vote_count=None,
            vote_sum=None,
            has_developer_response=False,
            developer_response_id=None,
            developer_response_date=None,
            provider="itunes",
        )
        for i in range(2)
    ]
    snapshot = CollectionResult(
        app=AppInfo(app_id=7, name="Fixture", country="us"),
        sampling=SamplingMetadata(
            provider="itunes",
            method="uniform_random_rank",
            storefront="us",
            population_total=2,
            reachable=2,
            frame_description="newest 2 of 2",
            frame_first_date=None,
            frame_last_date=None,
            requested=2,
            actual=2,
            sample_complete=True,
            sampling_fraction=1.0,
            seed=42,
            ranks=[0, 1],
            collected_at=datetime.now(UTC),
            store_histogram=[0, 0, 0, 0, 2],
            store_mean=5.0,
            store_rating_count=2,
        ),
        reviews=reviews,
    )
    path = tmp_path / "fixture.snapshot.json"
    path.write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")

    replay = FixtureProvider(tmp_path).sample(7, "us", 2, 42)
    assert replay == snapshot
    with pytest.raises(AppError) as exc_info:
        FixtureProvider(tmp_path).sample(7, "us", 2, 43)
    assert exc_info.value.code == "FIXTURE_NOT_AVAILABLE"


def test_window_days_90_selects_exact_newest_prefix() -> None:
    from datetime import timedelta

    now = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
    total = 10

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/lookup":
            return httpx.Response(200, json=_lookup_payload())
        if "/customer-reviews/" in path:
            return httpx.Response(200, json=_population_payload(total))
        if path.endswith("/userReviewsRow"):
            rank = int(request.url.params["startIndex"])
            row = _row(rank)
            row["date"] = (now - timedelta(days=rank * 15)).isoformat()
            return httpx.Response(200, json={"userReviewList": [row]})
        raise AssertionError(request.url)

    provider = ITunesProvider(
        _settings(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleeper=lambda _: None,
        now=lambda: now,
    )
    result = provider.sample(1459969523, "us", 100, 42, window_days=90)

    assert result.sampling.reachable == 7
    assert result.sampling.window_days == 90
    assert result.sampling.model_dump(mode="json")["window_days"] == 90
    assert result.sampling.ranks == list(range(7))
    assert [review.rank for review in result.reviews] == list(range(7))
    assert result.sampling.frame_last_date == now - timedelta(days=90)
    assert "last 90 days" in result.sampling.frame_description
    assert result.sampling.population_total == total
    assert result.sampling.actual == 7
    assert result.sampling.sample_complete is False


def test_429_with_long_retry_after_fails_fast_instead_of_blocking_the_request() -> None:
    sleeps: list[float] = []
    provider = ITunesProvider(
        _settings(http_max_retries=3),
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(429, headers={"Retry-After": "120"}, json={})
            )
        ),
        sleeper=sleeps.append,
    )
    with pytest.raises(AppError) as exc_info:
        provider.app_info(1459969523, "us")
    assert exc_info.value.code == "UPSTREAM_RATE_LIMITED"
    assert exc_info.value.retry_after == 120
    assert sleeps == []


def test_429_with_non_finite_retry_after_uses_bounded_backoff() -> None:
    sleeps: list[float] = []
    provider = ITunesProvider(
        _settings(http_max_retries=1),
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(429, headers={"Retry-After": "inf"}, json={})
            )
        ),
        sleeper=sleeps.append,
    )
    with pytest.raises(AppError) as exc_info:
        provider.app_info(1459969523, "us")
    assert exc_info.value.code == "UPSTREAM_RATE_LIMITED"
    assert exc_info.value.retry_after is None
    assert len(sleeps) == 1 and 0.0 <= sleeps[0] <= 8.0
