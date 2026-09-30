from datetime import UTC, datetime

import pytest

from appstore_review_analysis.analysis.metrics import (
    rating_metrics,
    rating_proportion_interval,
    wilson_interval,
)
from appstore_review_analysis.domain import Review, SamplingMetadata


def _review(index: int, rating: int, year: int = 2026) -> Review:
    return Review(
        source_review_id=f"r{index}",
        rank=index,
        country="us",
        title="title",
        body="body",
        rating=rating,
        created_at=datetime(year, 1, 1, tzinfo=UTC),
        is_edited=False,
        vote_count=0,
        vote_sum=0,
        has_developer_response=False,
        developer_response_id=None,
        developer_response_date=None,
        provider="fixture",
    )


def _sampling(*, requested: int, actual: int, reachable: int = 1000) -> SamplingMetadata:
    return SamplingMetadata(
        provider="fixture",
        method="fixture_replay",
        storefront="us",
        population_total=reachable,
        reachable=reachable,
        frame_description=f"newest {reachable} of {reachable}",
        frame_first_date=datetime(2026, 1, 1, tzinfo=UTC),
        frame_last_date=datetime(2020, 1, 1, tzinfo=UTC),
        requested=requested,
        actual=actual,
        sample_complete=actual == requested,
        sampling_fraction=(actual / reachable if reachable else None),
        seed=42,
        ranks=list(range(actual)),
        store_histogram=[10, 10, 10, 20, 50],
        store_mean=3.8,
        store_rating_count=100,
        collected_at=datetime(2026, 9, 29, tzinfo=UTC),
    )


def test_rating_metrics_reference_mean_and_distribution() -> None:
    ratings = [1] * 14 + [2] * 4 + [3] * 6 + [4] * 12 + [5] * 64
    reviews = [_review(i, rating) for i, rating in enumerate(ratings)]
    metrics = rating_metrics(reviews, _sampling(requested=100, actual=100), 42)

    assert metrics["mean"] == pytest.approx(4.08)
    distribution = metrics["distribution"]
    assert isinstance(distribution, dict)
    assert distribution["5"]["count"] == 64
    assert distribution["5"]["percentage"] == pytest.approx(0.64)
    assert metrics["store_benchmark"]["mean"] == 3.8
    assert metrics["periods"]


def test_wilson_interval_matches_reference() -> None:
    interval = wilson_interval(64, 100)
    assert interval is not None
    assert interval[0] == pytest.approx(0.54235, abs=1e-4)
    assert interval[1] == pytest.approx(0.72734, abs=1e-4)


def test_degenerate_mean_ci_is_null_with_reason() -> None:
    one = rating_metrics([_review(0, 5)], _sampling(requested=1, actual=1), 42)
    assert one["mean_ci95"] == {"low": None, "high": None, "reason": "degenerate"}

    all_five = [_review(i, 5) for i in range(10)]
    constant = rating_metrics(all_five, _sampling(requested=10, actual=10), 42)
    assert constant["mean_ci95"]["reason"] == "degenerate"


def test_zero_review_metrics_never_emit_nan() -> None:
    metrics = rating_metrics([], _sampling(requested=100, actual=0, reachable=0), 42)
    assert metrics["mean"] is None
    assert metrics["mean_ci95"]["reason"] == "no_reviews"
    assert all(item["percentage"] is None for item in metrics["distribution"].values())


def test_period_metrics_are_asserted_by_value() -> None:
    reviews = [
        *[_review(i, 5, year=2026) for i in range(15)],
        *[_review(15 + i, 1, year=2025) for i in range(15)],
    ]
    metrics = rating_metrics(reviews, _sampling(requested=30, actual=30), 42)
    periods = metrics["periods"]

    assert [item["label"] for item in periods] == ["2026", "2025"]
    assert [item["n"] for item in periods] == [15, 15]
    assert [item["mean"] for item in periods] == [5.0, 1.0]
    assert [item["one_two_star_share"] for item in periods] == [0.0, 1.0]


def test_wilson_interval_has_exact_bounds_at_zero_and_full_counts() -> None:
    for n in range(1, 201):
        zero = wilson_interval(0, n)
        full = wilson_interval(n, n)
        assert zero is not None and zero[0] == 0.0
        assert full is not None and full[1] == 1.0


@pytest.mark.parametrize("population", [120, 500])
def test_finite_population_interval_always_contains_the_observed_share(population: int) -> None:
    n = 100
    for successes in range(n + 1):
        ci = rating_proportion_interval(successes, n, population=population)
        assert ci["low"] <= successes / n <= ci["high"]


def test_period_intervals_are_null_when_the_whole_frame_was_sampled() -> None:
    ratings = [1] * 30 + [3] * 20 + [5] * 50
    reviews = [_review(i, rating, year=2026 if i % 2 else 2025) for i, rating in enumerate(ratings)]
    metrics = rating_metrics(reviews, _sampling(requested=100, actual=100, reachable=100), 42)

    assert metrics["mean_ci95"]["reason"] == "census"
    for period in metrics["periods"]:
        assert period["mean_ci95"] == {"low": None, "high": None, "reason": "census"}
        assert period["one_two_star_ci95"] == {"low": None, "high": None, "reason": "census"}
