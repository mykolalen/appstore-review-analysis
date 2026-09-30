from __future__ import annotations

from appstore_review_analysis.report.population import (
    interval_covers,
    population_values,
    simulate_ci_coverage,
)


def _population() -> dict[str, object]:
    return {
        "walked": 100,
        "written_review_mean": 3.0,
        "written_review_star_distribution": {
            "1": 20,
            "2": 20,
            "3": 20,
            "4": 20,
            "5": 20,
        },
    }


def test_population_values_validate_star_counts() -> None:
    walked, counts = population_values(_population())
    assert walked == 100
    assert counts == (20, 20, 20, 20, 20)


def test_population_coverage_simulation_is_seeded_and_bounded() -> None:
    first = simulate_ci_coverage(_population(), sample_size=25, simulations=20, seed_base=123)
    second = simulate_ci_coverage(_population(), sample_size=25, simulations=20, seed_base=123)
    assert first == second
    assert first["simulations"] == 20
    assert first["sample_size"] == 25
    mean_rate = first["mean"]["coverage_rate"]
    assert isinstance(mean_rate, float)
    assert 0.0 <= mean_rate <= 1.0
    for star in map(str, range(1, 6)):
        rate = first["stars"][star]["coverage_rate"]
        assert isinstance(rate, float)
        assert 0.0 <= rate <= 1.0


def test_interval_coverage_reports_true_false_or_unavailable() -> None:
    assert interval_covers({"low": 0.2, "high": 0.4}, 0.3) is True
    assert interval_covers({"low": 0.2, "high": 0.4}, 0.5) is False
    assert interval_covers({"low": None, "high": None, "reason": "degenerate"}, 0.3) is None
