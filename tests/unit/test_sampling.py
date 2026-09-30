import json
import random

from appstore_review_analysis.collection.sampling import (
    MAX_JSON_SAFE_SEED,
    resolve_seed,
    sample_ranks,
)


def test_sample_ranks_is_unique_in_range_and_reproducible() -> None:
    first = sample_ranks(1000, 100, random.Random(42))
    second = sample_ranks(1000, 100, random.Random(42))
    third = sample_ranks(1000, 100, random.Random(43))

    assert first == second
    assert first != third
    assert len(first) == len(set(first)) == 100
    assert all(0 <= rank < 1000 for rank in first)


def test_sample_ranks_returns_census_when_k_reaches_population() -> None:
    assert sample_ranks(5, 5, random.Random(42)) == [0, 1, 2, 3, 4]
    assert sample_ranks(5, 99, random.Random(42)) == [0, 1, 2, 3, 4]
    assert sample_ranks(0, 100, random.Random(42)) == []


def test_generated_seed_round_trips_through_float64_and_json() -> None:
    seed = resolve_seed(None)
    assert 0 <= seed < MAX_JSON_SAFE_SEED
    assert int(float(seed)) == seed
    encoded = json.dumps({"seed": seed})
    assert json.loads(encoded)["seed"] == seed
