"""Deterministic rating/statistical metrics for one sampled review set."""

from __future__ import annotations

import math
from collections import Counter
from statistics import median
from typing import Any

import numpy as np
from scipy.stats import bootstrap

from appstore_review_analysis.domain import Review, SamplingMetadata

_Z_95 = 1.959963984540054
_RATING_MEAN_STAGE_ID = 1


def rating_metrics(reviews: list[Review], sampling: SamplingMetadata, seed: int) -> dict[str, Any]:
    """Calculate headline, distribution, store benchmark and time-period metrics."""

    ratings = [review.rating for review in reviews]
    n = len(ratings)
    sampling_fraction = sampling.sampling_fraction
    census = bool(sampling.reachable and n == sampling.reachable)

    if n == 0:
        mean_value: float | None = None
        median_value: float | None = None
        std_value: float | None = None
        mean_ci = _null_ci("no_reviews")
    else:
        mean_value = float(np.mean(ratings))
        median_value = float(median(ratings))
        std_value = float(np.std(ratings, ddof=1)) if n > 1 else None
        mean_ci = _mean_ci(
            ratings,
            seed=seed,
            stage_id=_RATING_MEAN_STAGE_ID,
            population=sampling.reachable,
            census=census,
        )

    counts = Counter(ratings)
    distribution: dict[str, Any] = {}
    for star in range(1, 6):
        count = counts.get(star, 0)
        distribution[str(star)] = {
            "count": count,
            "percentage": (count / n if n else None),
            "ci95": _proportion_ci(
                count,
                n,
                population=sampling.reachable,
                census=census,
            ),
        }

    return {
        "n": n,
        "mean": mean_value,
        "mean_ci95": mean_ci,
        "median": median_value,
        "stddev": std_value,
        "distribution": distribution,
        "sampling_fraction": sampling_fraction,
        "store_benchmark": {
            "mean": sampling.store_mean,
            "rating_count": sampling.store_rating_count,
            "histogram_1_to_5": sampling.store_histogram,
            "note": (
                "Store ratings include star-only ratings and are not the same population as "
                "sampled written reviews."
            ),
        },
        "periods": _period_metrics(reviews, sampling, seed),
        "bootstrap": {
            "method": "BCa",
            "n_resamples": 9999,
            "seed": [seed, _RATING_MEAN_STAGE_ID],
        },
    }


def wilson_interval(successes: int, n: int) -> tuple[float, float] | None:
    """Uncorrected two-sided Wilson 95% interval, exposed for reference tests."""

    if n <= 0:
        return None
    p = successes / n
    z2 = _Z_95 * _Z_95
    denominator = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denominator
    half = _Z_95 * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n) / denominator
    return max(0.0, center - half), min(1.0, center + half)


def _proportion_ci(
    successes: int,
    n: int,
    *,
    population: int,
    census: bool,
) -> dict[str, Any]:
    if n == 0:
        return _null_ci("no_reviews")
    if census:
        return _null_ci("census")
    interval = wilson_interval(successes, n)
    assert interval is not None
    low, high = interval
    if population > 1 and n / population > 0.05:
        correction = math.sqrt(max(0.0, (population - n) / (population - 1)))
        center = (low + high) / 2.0
        half = (high - low) / 2.0 * correction
        low, high = max(0.0, center - half), min(1.0, center + half)
    return {"low": low, "high": high, "reason": None}


def _mean_ci(
    values: list[int],
    *,
    seed: int,
    stage_id: int,
    population: int,
    census: bool,
) -> dict[str, Any]:
    if not values:
        return _null_ci("no_reviews")
    if census:
        return _null_ci("census")
    if len(values) < 2 or len(set(values)) == 1:
        return _null_ci("degenerate")
    array = np.asarray(values, dtype=float)
    try:
        result = bootstrap(
            (array,),
            np.mean,
            method="BCa",
            n_resamples=9999,
            rng=np.random.default_rng([seed, stage_id]),
        )
    except (FloatingPointError, ValueError):
        return _null_ci("degenerate")
    low = float(result.confidence_interval.low)
    high = float(result.confidence_interval.high)
    if not (math.isfinite(low) and math.isfinite(high)):
        return _null_ci("degenerate")
    n = len(values)
    if population > 1 and n / population > 0.05:
        correction = math.sqrt(max(0.0, (population - n) / (population - 1)))
        center = float(np.mean(array))
        low = center - (center - low) * correction
        high = center + (high - center) * correction
    return {"low": low, "high": high, "reason": None}


def _period_metrics(
    reviews: list[Review],
    sampling: SamplingMetadata,
    seed: int,
) -> list[dict[str, Any]]:
    dated = [review for review in reviews if review.created_at is not None]
    if not dated:
        return []
    by_year: dict[int, list[Review]] = {}
    for review in dated:
        assert review.created_at is not None
        by_year.setdefault(review.created_at.year, []).append(review)
    buckets = _merge_years(by_year, minimum=15)
    result: list[dict[str, Any]] = []
    for index, bucket in enumerate(buckets):
        years = sorted(
            {review.created_at.year for review in bucket if review.created_at},
            reverse=True,
        )
        label = str(years[0]) if len(years) == 1 else f"{min(years)}-{max(years)}"
        ratings = [review.rating for review in bucket]
        low_ratings = sum(rating <= 2 for rating in ratings)
        result.append(
            {
                "label": label,
                "years": sorted(years),
                "n": len(bucket),
                "mean": float(np.mean(ratings)),
                "mean_ci95": _mean_ci(
                    ratings,
                    seed=seed,
                    stage_id=100 + index,
                    population=max(sampling.reachable, len(bucket)),
                    census=False,
                ),
                "one_two_star_share": low_ratings / len(bucket),
                "one_two_star_ci95": _proportion_ci(
                    low_ratings,
                    len(bucket),
                    population=max(sampling.reachable, len(bucket)),
                    census=False,
                ),
            }
        )
    return result


def _merge_years(by_year: dict[int, list[Review]], minimum: int) -> list[list[Review]]:
    buckets: list[list[Review]] = []
    current: list[Review] = []
    for year in sorted(by_year, reverse=True):
        current.extend(by_year[year])
        if len(current) >= minimum:
            buckets.append(current)
            current = []
    if current:
        if buckets:
            buckets[-1].extend(current)
        else:
            buckets.append(current)
    return buckets


def _null_ci(reason: str) -> dict[str, Any]:
    return {"low": None, "high": None, "reason": reason}


def rating_mean_interval(
    values: list[int], *, seed: int, population: int, census: bool = False
) -> dict[str, Any]:
    """Expose the same seeded BCa mean interval used by the rating metrics."""

    return _mean_ci(
        values,
        seed=seed,
        stage_id=_RATING_MEAN_STAGE_ID,
        population=population,
        census=census,
    )


def rating_proportion_interval(
    successes: int, n: int, *, population: int, census: bool = False
) -> dict[str, Any]:
    """Expose the same Wilson/FPC interval used by the rating distribution."""

    return _proportion_ci(successes, n, population=population, census=census)
