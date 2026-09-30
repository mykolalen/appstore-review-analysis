"""Population-check helpers used by the deterministic report renderer."""

from __future__ import annotations

import random
from collections import Counter
from functools import lru_cache
from typing import Any

from appstore_review_analysis.analysis.metrics import (
    rating_mean_interval,
    rating_proportion_interval,
)
from appstore_review_analysis.collection.sampling import sample_ranks

POPULATION_COVERAGE_SIMULATIONS = 1_000
POPULATION_COVERAGE_SEED_BASE = 20_260_928


def population_values(population: dict[str, Any]) -> tuple[int, tuple[int, int, int, int, int]]:
    """Return walked total and 1-to-5 star counts from the committed aggregate."""

    raw = population.get("written_review_star_distribution")
    if not isinstance(raw, dict):
        raise ValueError("Population aggregate is missing written_review_star_distribution.")
    counts = tuple(_int(raw.get(str(star))) for star in range(1, 6))
    walked = _int(population.get("walked"))
    if walked <= 0 or sum(counts) != walked:
        raise ValueError("Population star counts must sum to the walked review count.")
    return walked, counts  # type: ignore[return-value]


def simulate_ci_coverage(
    population: dict[str, Any],
    *,
    sample_size: int,
    simulations: int = POPULATION_COVERAGE_SIMULATIONS,
    seed_base: int = POPULATION_COVERAGE_SEED_BASE,
) -> dict[str, object]:
    """Simulate the project's BCa/Wilson interval coverage from aggregate star counts."""

    walked, counts = population_values(population)
    if sample_size <= 0:
        raise ValueError("sample_size must be positive for coverage simulation.")
    if simulations <= 0:
        raise ValueError("simulations must be positive.")
    k = min(sample_size, walked)
    return _simulate_ci_coverage_cached(counts, walked, k, simulations, seed_base)


@lru_cache(maxsize=16)
def _simulate_ci_coverage_cached(
    counts: tuple[int, int, int, int, int],
    walked: int,
    sample_size: int,
    simulations: int,
    seed_base: int,
) -> dict[str, object]:
    ratings: list[int] = []
    for star, count in enumerate(counts, start=1):
        ratings.extend([star] * count)
    population_mean = sum(star * count for star, count in enumerate(counts, start=1)) / walked
    population_shares = {str(star): counts[star - 1] / walked for star in range(1, 6)}

    mean_covered = 0
    mean_evaluable = 0
    star_covered = {str(star): 0 for star in range(1, 6)}
    star_evaluable = {str(star): 0 for star in range(1, 6)}

    for index in range(simulations):
        seed = seed_base + index
        ranks = sample_ranks(walked, sample_size, random.Random(seed))
        sample = [ratings[rank] for rank in ranks]

        mean_ci = rating_mean_interval(sample, seed=seed, population=walked)
        if _covers(mean_ci, population_mean):
            mean_covered += 1
        if _interval_is_evaluable(mean_ci):
            mean_evaluable += 1

        sample_counts = Counter(sample)
        for star in range(1, 6):
            key = str(star)
            ci = rating_proportion_interval(
                sample_counts.get(star, 0),
                len(sample),
                population=walked,
            )
            if _covers(ci, population_shares[key]):
                star_covered[key] += 1
            if _interval_is_evaluable(ci):
                star_evaluable[key] += 1

    return {
        "simulations": simulations,
        "sample_size": sample_size,
        "seed_base": seed_base,
        "mean": {
            "covered": mean_covered,
            "evaluable": mean_evaluable,
            "coverage_rate": mean_covered / mean_evaluable if mean_evaluable else None,
        },
        "stars": {
            key: {
                "covered": star_covered[key],
                "evaluable": star_evaluable[key],
                "coverage_rate": (
                    star_covered[key] / star_evaluable[key] if star_evaluable[key] else None
                ),
            }
            for key in star_covered
        },
    }


def interval_covers(ci: dict[str, Any], value: float | None) -> bool | None:
    """Return whether a numeric CI covers a value, or None if either is unavailable."""

    if value is None or not _interval_is_evaluable(ci):
        return None
    low = float(ci["low"])
    high = float(ci["high"])
    return low <= value <= high


def _covers(ci: dict[str, Any], value: float) -> bool:
    covered = interval_covers(ci, value)
    return bool(covered) if covered is not None else False


def _interval_is_evaluable(ci: dict[str, Any]) -> bool:
    return isinstance(ci.get("low"), (int, float)) and isinstance(ci.get("high"), (int, float))


def _int(value: object) -> int:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else 0
