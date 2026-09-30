"""Golden/report rendering checks driven by the real analysis pipeline."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from appstore_review_analysis.analysis.pipeline import analyse_collection
from appstore_review_analysis.analysis.sentiment import FakeSentiment
from appstore_review_analysis.analysis.themes import FakeEmbedder
from appstore_review_analysis.collection.fixture import FixtureProvider
from appstore_review_analysis.report.charts import (
    issue_category_support_data,
    period_rating_data,
    rating_distribution_data,
    sentiment_distribution_data,
)
from appstore_review_analysis.report.contracts import ReportAnalysisContract
from appstore_review_analysis.report.render import render_report, write_report_files

ROOT = Path(__file__).resolve().parents[2]


class _EnglishIdentifier:
    def classify(self, _text: str) -> tuple[str, float]:
        return "en", 0.99


def _pipeline_analysis(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    monkeypatch.setattr(
        "appstore_review_analysis.text._language_identifier",
        lambda: _EnglishIdentifier(),
    )
    provider = FixtureProvider(ROOT / "data/fixtures")
    collection = provider.sample(1459969523, "us", 100, 42)
    analysis, _rows = analyse_collection(
        collection,
        request_payload={
            "app": "1459969523",
            "country": "us",
            "sample_size": 100,
            "seed": 42,
            "provider": "fixture",
            "analyze": True,
        },
        sentiment=FakeSentiment(),
        embedder=FakeEmbedder(),
        analyze=True,
        request_deadline_s=90,
    )
    payload = analysis.model_dump(mode="json")
    ReportAnalysisContract.model_validate(payload)
    return payload


def _population() -> dict[str, object]:
    return json.loads((ROOT / "reports/population_us.json").read_text(encoding="utf-8"))


def _chart_links() -> dict[str, str]:
    return {
        "rating_distribution": "charts/rating_distribution.png",
        "sentiment_distribution": "charts/sentiment_distribution.png",
        "issue_category_support": "charts/issue_category_support.png",
        "rating_by_period": "charts/rating_by_period.png",
    }


def test_pipeline_output_satisfies_report_contract_and_has_no_empty_sections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analysis = _pipeline_analysis(monkeypatch)
    markdown = render_report(
        analysis,
        population=_population(),
        evaluation=None,
        chart_links=_chart_links(),
    )

    non_blank = [line.strip() for line in markdown.splitlines() if line.strip()]
    for current, following in zip(non_blank, non_blank[1:], strict=False):
        if current.startswith("##"):
            assert not following.startswith("#"), f"empty report section after {current!r}"
    assert "| n/a |" not in markdown.lower()
    assert "## Preprocessing" in markdown
    assert "### Before/after examples" in markdown
    assert "Theme coverage:" in markdown
    assert "### Issue categories" in markdown
    assert "### Emerging clusters" in markdown
    assert "### Not categorised" in markdown
    assert "Category precision audit: **not audited**" in markdown


def test_committed_report_has_no_empty_sections_or_unexplained_na() -> None:
    markdown = (ROOT / "reports/nebula_us_seed42.md").read_text(encoding="utf-8")
    non_blank = [line.strip() for line in markdown.splitlines() if line.strip()]
    for current, following in zip(non_blank, non_blank[1:], strict=False):
        if current.startswith("##"):
            assert not following.startswith("#"), f"empty report section after {current!r}"
    assert "| n/a |" not in markdown.lower()
    assert "none met the precision target" in markdown.lower()


def test_report_explains_recent_window_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    analysis = _pipeline_analysis(monkeypatch)
    analysis["request"]["provider"] = "itunes"  # type: ignore[index]
    analysis["sampling"]["window_days"] = 90  # type: ignore[index]
    analysis["sampling"]["reachable"] = 37  # type: ignore[index]
    analysis["sampling"]["frame_description"] = (  # type: ignore[index]
        "newest 37 reviews within the last 90 days of 15,002 written reviews"
    )

    markdown = render_report(
        analysis,
        population=_population(),
        evaluation=None,
        chart_links=_chart_links(),
    )

    assert "### Recent-window frame" in markdown
    assert "last **90 days**" in markdown
    assert "**37 reviews**" in markdown


def test_chart_data_matches_pipeline_analysis_json_exactly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analysis = _pipeline_analysis(monkeypatch)
    ratings = rating_distribution_data(analysis)
    sentiment = sentiment_distribution_data(analysis)
    categories = issue_category_support_data(analysis)
    periods = period_rating_data(analysis)

    distribution = analysis["metrics"]["distribution"]  # type: ignore[index]
    assert ratings["counts"] == [distribution[str(star)]["count"] for star in range(1, 6)]
    assert ratings["values"] == [distribution[str(star)]["percentage"] for star in range(1, 6)]

    sentiment_distribution = analysis["sentiment"]["distribution"]  # type: ignore[index]
    assert sentiment["counts"] == [
        sentiment_distribution[label]["count"] for label in ("negative", "neutral", "positive")
    ]

    category_items = analysis["insights"]["issue_categories"]["items"]  # type: ignore[index]
    assert categories["counts"] == [item["review_count"] for item in category_items]
    assert categories["values"] == [item["share"] for item in category_items]
    assert categories["low"] == [item["ci95"]["low"] for item in category_items]
    assert categories["high"] == [item["ci95"]["high"] for item in category_items]

    metric_periods = analysis["metrics"]["periods"]  # type: ignore[index]
    assert periods["counts"] == [item["n"] for item in metric_periods]
    assert periods["values"] == [item["mean"] for item in metric_periods]


def test_report_writes_four_non_empty_charts_and_links_them(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analysis_path = tmp_path / "analysis.json"
    population_path = tmp_path / "population.json"
    evaluation_path = tmp_path / "missing-evaluation.json"
    report_path = tmp_path / "report.md"
    charts_dir = tmp_path / "charts"
    analysis = _pipeline_analysis(monkeypatch)
    analysis_path.write_text(json.dumps(analysis), encoding="utf-8")
    population_path.write_text(json.dumps(_population()), encoding="utf-8")

    written, charts = write_report_files(
        analysis_path=analysis_path,
        population_path=population_path,
        evaluation_path=evaluation_path,
        output_path=report_path,
        charts_dir=charts_dir,
    )

    markdown = written.read_text(encoding="utf-8")
    assert "## Areas of improvement" in markdown
    assert "llm_status" not in markdown
    assert "Offline coverage simulation" in markdown
    assert "CI covers population?" in markdown
    assert "Mean rating" in markdown
    for star in range(1, 6):
        assert f"{star}-star share" in markdown
    assert "1,000" in markdown
    assert len(charts) == 4
    assert "issue_category_support" in charts
    assert not (charts_dir / "theme_support.png").exists()
    for chart in charts.values():
        assert chart.stat().st_size > 1000
        assert f"charts/{chart.name}" in markdown


def test_committed_report_is_golden_render_of_committed_inputs() -> None:
    analysis_path = ROOT / "reports/nebula_us_seed42.analysis.json"
    population_path = ROOT / "reports/population_us.json"
    report_path = ROOT / "reports/nebula_us_seed42.md"
    assert analysis_path.exists()
    assert population_path.exists()
    assert report_path.exists()

    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    population = json.loads(population_path.read_text(encoding="utf-8"))
    evaluation_path = ROOT / "evaluation/results.json"
    evaluation = (
        json.loads(evaluation_path.read_text(encoding="utf-8"))
        if evaluation_path.exists()
        else None
    )
    ReportAnalysisContract.model_validate(analysis)
    expected = render_report(
        analysis,
        population=population,
        evaluation=evaluation,
        chart_links=_chart_links(),
    )
    assert report_path.read_text(encoding="utf-8") == expected


def test_report_marks_missing_evaluation_parts_explicitly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analysis = _pipeline_analysis(monkeypatch)
    evaluation = {
        "benchmark": {"status": "not_run", "reason": "labels missing"},
        "annotator_relabel": {"status": "not_run", "reason": "later-session labels missing"},
        "complaint_units": {"status": "not_run", "reason": "units_gold.csv missing"},
        "theme_threshold": {"status": "not_run", "reason": "pairs_gold.csv missing"},
    }
    markdown = render_report(
        analysis,
        population=_population(),
        evaluation=evaluation,
        chart_links=_chart_links(),
    )

    assert "Sentiment benchmark: **evaluation not run**" in markdown
    assert "Anchoring" not in markdown
    assert "Annotator repeatability: **evaluation not run**" in markdown
    assert "Complaint-unit check: **evaluation not run**" in markdown
    assert "Theme-threshold check: **evaluation not run**" in markdown
    assert "Issue-category precision audit: **not audited**" in markdown


def test_report_renders_structured_evaluation_without_json_dump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analysis = _pipeline_analysis(monkeypatch)
    evaluation = {
        "benchmark": {
            "status": "run",
            "evaluable_n": 120,
            "mixed_n": 30,
            "neutral_note": (
                "The neutral class is too small for a reliable standalone performance conclusion."
            ),
            "decision_rule": (
                "Keep the shipping model unless the challenger improvement CI excludes zero."
            ),
            "paired_tabularis_minus_shipping_negative_f1_ci95": {
                "low": -0.02,
                "high": 0.07,
                "reason": None,
            },
            "models": [
                {
                    "name": "shipping_cardiffnlp",
                    "metrics": {"accuracy": 0.8, "macro_f1": 0.75, "negative_f1": 0.82},
                }
            ],
        },
        "annotator_relabel": {"status": "run", "n": 30, "kappa": 0.8},
        "complaint_units": {
            "status": "run",
            "precision": {"value": 0.85},
            "recall": {"value": 0.75},
            "subgroups": {},
        },
        "theme_threshold": {"status": "run", "selected_threshold": 0.45},
        "issue_categories": {
            "status": "run",
            "categories": [
                {
                    "category": "billing_charges",
                    "n": 10,
                    "precision": {"value": 0.9, "low": 0.6, "high": 0.98},
                }
            ],
        },
    }
    markdown = render_report(
        analysis,
        population=_population(),
        evaluation=evaluation,
        chart_links=_chart_links(),
    )
    assert "shipping_cardiffnlp" in markdown
    assert "Negative F1" in markdown
    assert "Anchoring" not in markdown
    assert "repeat-label file does not encode session timing" in markdown
    assert "selected threshold **0.45**" in markdown
    assert "Issue-category precision audit:" in markdown
    assert "billing_charges" in markdown
    assert '"benchmark"' not in markdown


def _category_audit(*, pricing_correct: int) -> dict[str, object]:
    return {
        "status": "run",
        "n": 20,
        "categories": [
            {
                "category": "billing_charges",
                "n": 10,
                "correct": 9,
                "precision": {"value": 0.9, "low": 0.6, "high": 0.98},
            },
            {
                "category": "pricing_paywall",
                "n": 10,
                "correct": pricing_correct,
                "precision": {"value": pricing_correct / 10, "low": 0.3, "high": 0.9},
            },
        ],
        "overall_precision": {"value": 0.8, "low": 0.6, "high": 0.92},
    }


def test_limitations_report_measured_category_precision_and_name_weak_categories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analysis = _pipeline_analysis(monkeypatch)

    markdown = render_report(
        analysis,
        population=_population(),
        evaluation={"issue_categories": _category_audit(pricing_correct=6)},
        chart_links=_chart_links(),
    )

    assert "measured on 20 human-labelled category matches: overall 80.0%" in markdown
    assert "Categories below 80% precision: pricing_paywall 60.0% (6/10)" in markdown
    assert "billing_charges 90.0%" not in markdown.split("## Limitations", 1)[1]
    assert "has not been measured; the optional human audit" not in markdown


def test_limitations_state_when_no_audited_category_is_below_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analysis = _pipeline_analysis(monkeypatch)

    markdown = render_report(
        analysis,
        population=_population(),
        evaluation={"issue_categories": _category_audit(pricing_correct=9)},
        chart_links=_chart_links(),
    )

    assert "No audited category fell below 80% precision." in markdown
    assert "Categories below" not in markdown
