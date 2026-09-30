"""Render the reproducible Nebula analysis report from committed JSON inputs."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from appstore_review_analysis.report.charts import render_all_charts
from appstore_review_analysis.report.constants import (
    RATING_INTERVAL,
    REPRODUCTION_NOTE,
    SAMPLING_METHOD,
    SENTIMENT_MODEL_DOMAIN_CAVEAT,
    SOURCE_CAVEAT,
    SOURCE_NAME,
)
from appstore_review_analysis.report.contracts import ReportAnalysisContract
from appstore_review_analysis.report.population import interval_covers, simulate_ci_coverage

_CATEGORY_PRECISION_TARGET = 0.80

_PREPROCESSING_DENOMINATORS = (
    ("n_all", "All sampled reviews"),
    ("n_analysable", "Analysable reviews"),
    ("n_negative", "Model-negative reviews"),
    ("n_rest", "Other analysable reviews"),
    ("n_complaint_reviews", "Reviews with complaint units"),
    ("n_units", "Complaint units"),
)


def load_json(path: Path, *, required: bool = True) -> dict[str, Any] | None:
    """Load a UTF-8 JSON object, optionally returning None when the file is absent."""

    if not path.exists():
        if required:
            raise FileNotFoundError(path)
        return None
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def render_report(
    analysis: dict[str, Any],
    *,
    population: dict[str, Any] | None,
    evaluation: dict[str, Any] | None,
    chart_links: dict[str, str],
) -> str:
    """Return deterministic Markdown for one validated analysis payload."""

    ReportAnalysisContract.model_validate(analysis)
    lines: list[str] = []
    app = _dict(analysis.get("app"))
    sampling = _dict(analysis.get("sampling"))
    metrics = _dict(analysis.get("metrics"))
    preprocessing = _dict(analysis.get("preprocessing"))
    sentiment = _dict(analysis.get("sentiment"))
    keywords = _dict(analysis.get("keywords"))
    themes = _dict(analysis.get("themes"))
    insights = _dict(analysis.get("insights"))
    provenance = _dict(analysis.get("provenance"))

    title_name = str(app.get("name") or "App Store app")
    lines.extend([f"# {title_name} - App Store review analysis", ""])
    lines.extend(_executive_summary(metrics, sentiment, insights, themes))
    lines.extend(_dataset_and_provenance(analysis, app, sampling, provenance))
    lines.extend(_recent_window_frame(analysis, sampling))
    lines.extend(_ratings(metrics, population, chart_links))
    lines.extend(_periods(metrics, chart_links))
    lines.extend(_preprocessing(preprocessing))
    lines.extend(_sentiment(sentiment, evaluation, chart_links))
    lines.extend(_keywords(keywords))
    lines.extend(_areas(insights, themes, evaluation, chart_links))
    lines.extend(_limitations(population, evaluation, themes))
    lines.extend(_methodology(themes))
    lines.extend(_reproduce())
    return "\n".join(lines).rstrip() + "\n"


def write_report_files(
    *,
    analysis_path: Path,
    population_path: Path,
    evaluation_path: Path,
    output_path: Path,
    charts_dir: Path,
) -> tuple[Path, dict[str, Path]]:
    """Load committed inputs, render four charts and write the Markdown report."""

    analysis = load_json(analysis_path)
    assert analysis is not None
    population = load_json(population_path, required=False)
    evaluation = load_json(evaluation_path, required=False)
    ReportAnalysisContract.model_validate(analysis)
    chart_paths = render_all_charts(analysis, charts_dir)
    links = {
        key: _relative_markdown_path(path, output_path.parent) for key, path in chart_paths.items()
    }
    markdown = render_report(
        analysis,
        population=population,
        evaluation=evaluation,
        chart_links=links,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown, encoding="utf-8", newline="\n")
    return output_path, chart_paths


def _executive_summary(
    metrics: dict[str, Any],
    sentiment: dict[str, Any],
    insights: dict[str, Any],
    themes: dict[str, Any],
) -> list[str]:
    mean = _number(metrics.get("mean"))
    n = _integer(metrics.get("n"))
    negative = _dict(_dict(sentiment.get("distribution")).get("negative"))
    negative_share = _number(negative.get("percentage"))
    areas = [
        item
        for item in _list(insights.get("areas_of_improvement"))
        if isinstance(item, dict) and item.get("source") == "issue_category"
    ]
    lines = ["## Executive summary", ""]
    summary_bits = [f"The report analyses **{n} sampled written reviews**."]
    if mean is not None:
        summary_bits.append(f"The sampled mean rating is **{mean:.2f}/5**.")
    if negative_share is not None:
        summary_bits.append(
            f"Model-negative sentiment accounts for **{negative_share:.1%}** of analysable reviews."
        )
    lines.extend([" ".join(summary_bits), ""])
    if areas:
        lines.append("Highest-supported issue categories, ordered with recent evidence first:")
        lines.append("")
        for area in areas[:5]:
            complaint = _dict(area.get("complaint_reviews"))
            count = _integer(complaint.get("count"))
            total = _integer(complaint.get("total"))
            share = _fmt_pct(complaint.get("share"))
            ci = _fmt_ci(_dict(complaint.get("ci95")), percent=True)
            lines.append(
                f"- **{_safe(str(area.get('area') or 'Issue category'))}**: "
                f"{count} of {total} complaint reviews ({share}; 95% CI {ci})."
            )
        lines.append("")
    else:
        lines.extend(["No issue category met the two-review support threshold.", ""])
    return lines


def _recent_window_frame(analysis: dict[str, Any], sampling: dict[str, Any]) -> list[str]:
    request = _dict(analysis.get("request"))
    raw_window_days = request.get("window_days", sampling.get("window_days"))
    if raw_window_days is None:
        return []
    window_days = _integer(raw_window_days)
    if window_days < 1:
        return []
    return [
        "### Recent-window frame",
        "",
        (
            f"This analysis samples only the newest written reviews dated within the last "
            f"**{window_days} days**. The selected frame contains "
            f"**{_fmt_int(sampling.get('reachable'))} reviews** and is recorded as "
            f"`{_safe(str(sampling.get('frame_description') or 'unknown'))}`."
        ),
        "",
    ]


def _dataset_and_provenance(
    analysis: dict[str, Any],
    app: dict[str, Any],
    sampling: dict[str, Any],
    provenance: dict[str, Any],
) -> list[str]:
    lines = ["## Dataset and provenance", ""]
    rows = [
        ("App", str(app.get("name") or app.get("app_id") or "unknown")),
        ("App Store ID", str(app.get("app_id") or "unknown")),
        ("Storefront", str(sampling.get("storefront") or app.get("country") or "unknown")),
        ("Provider", str(sampling.get("provider") or "unknown")),
        ("Sampling method", str(sampling.get("method") or SAMPLING_METHOD)),
        ("Frame", str(sampling.get("frame_description") or "unknown")),
        ("Population total", _fmt_int(sampling.get("population_total"))),
        ("Reachable frame", _fmt_int(sampling.get("reachable"))),
        (
            "Requested / actual",
            f"{_fmt_int(sampling.get('requested'))} / {_fmt_int(sampling.get('actual'))}",
        ),
        ("Sampling fraction", _fmt_pct(sampling.get("sampling_fraction"))),
        ("Seed", str(sampling.get("seed") if sampling.get("seed") is not None else "unknown")),
        ("Collected at", str(sampling.get("collected_at") or "unknown")),
        ("Analysis complete", str(bool(analysis.get("analysis_complete")))),
    ]
    lines.extend(_table(["Field", "Value"], rows))
    lines.extend(["", f"**Source:** {SOURCE_NAME}.", "", SOURCE_CAVEAT, ""])
    model_rows: list[tuple[str, str]] = []
    sentiment_model = _dict(provenance.get("sentiment_model"))
    embedding_model = _dict(provenance.get("embedding_model"))
    if sentiment_model:
        model_rows.append(
            (
                "Sentiment",
                f"{sentiment_model.get('id')} @ {sentiment_model.get('revision')}",
            )
        )
    if embedding_model:
        model_rows.append(
            (
                "Embeddings",
                f"{embedding_model.get('id')} @ {embedding_model.get('revision')}",
            )
        )
    if model_rows:
        lines.extend(["Model provenance:", ""])
        lines.extend(_table(["Component", "Pinned model"], model_rows))
        lines.append("")
    return lines


def _ratings(
    metrics: dict[str, Any], population: dict[str, Any] | None, chart_links: dict[str, str]
) -> list[str]:
    lines = ["## Ratings", ""]
    mean = _number(metrics.get("mean"))
    ci = _dict(metrics.get("mean_ci95"))
    store = _dict(metrics.get("store_benchmark"))
    rows = [
        ("Sample size", _fmt_int(metrics.get("n"))),
        ("Mean rating", _fmt_float(mean, 2)),
        ("Mean 95% CI", _fmt_ci(ci, decimals=2)),
        ("Median", _fmt_float(_number(metrics.get("median")), 2)),
        ("Std. dev.", _fmt_float(_number(metrics.get("stddev")), 2)),
        ("Store benchmark mean", _fmt_float(_number(store.get("mean")), 4)),
        ("Store rating count", _fmt_int(store.get("rating_count"))),
    ]
    lines.extend(_table(["Metric", "Value"], rows))
    lines.extend(["", f"![Rating distribution]({chart_links['rating_distribution']})", ""])
    lines.append("Sample rating distribution:")
    lines.append("")
    distribution = _dict(metrics.get("distribution"))
    dist_rows: list[tuple[str, ...]] = []
    for star in range(1, 6):
        row = _dict(distribution.get(str(star)))
        dist_rows.append(
            (
                str(star),
                _fmt_int(row.get("count")),
                _fmt_pct(row.get("percentage")),
                _fmt_ci(_dict(row.get("ci95")), percent=True),
            )
        )
    lines.extend(_table(["Stars", "Count", "Share", "95% CI"], dist_rows))
    lines.append("")
    lines.extend(_population_check(metrics, population))
    return lines


def _population_check(metrics: dict[str, Any], population: dict[str, Any] | None) -> list[str]:
    if population is None:
        return [
            "### Population check",
            "",
            "Population walk aggregate is unavailable; no written-review population "
            "check is shown.",
            "",
        ]
    population_mean = _number(population.get("written_review_mean"))
    sample_mean = _number(metrics.get("mean"))
    lines = ["### Population check", ""]
    text = (
        f"The one-off population walk contains **{_fmt_int(population.get('walked'))}** written "
        f"reviews with a mean of **{_fmt_float(population_mean, 2)}**."
    )
    if population_mean is not None and sample_mean is not None:
        text += f" The sampled mean differs by **{sample_mean - population_mean:+.2f} stars**."
    lines.extend([text, ""])

    star_counts = _dict(population.get("written_review_star_distribution"))
    walked = _integer(population.get("walked"))
    distribution = _dict(metrics.get("distribution"))
    comparison_rows: list[tuple[str, ...]] = []
    mean_ci = _dict(metrics.get("mean_ci95"))
    comparison_rows.append(
        (
            "Mean rating",
            _fmt_float(population_mean, 4),
            _fmt_ci(mean_ci, decimals=2),
            _coverage_word(interval_covers(mean_ci, population_mean)),
        )
    )
    for star in range(1, 6):
        sample = _dict(distribution.get(str(star)))
        population_share = _integer(star_counts.get(str(star))) / walked if walked else None
        sample_ci = _dict(sample.get("ci95"))
        comparison_rows.append(
            (
                f"{star}-star share",
                _fmt_pct(population_share),
                _fmt_ci(sample_ci, percent=True),
                _coverage_word(interval_covers(sample_ci, population_share)),
            )
        )
    lines.extend(
        _table(
            ["Metric", "Walked population", "Sample 95% CI", "CI covers population?"],
            comparison_rows,
        )
    )
    lines.append("")

    simulation = simulate_ci_coverage(population, sample_size=_integer(metrics.get("n")))
    lines.extend(
        [
            "Offline coverage simulation from the committed aggregate star distribution "
            f"(**{simulation['simulations']:,}** seeded samples, n={simulation['sample_size']}; "
            "sampling without replacement; the same BCa/Wilson CI functions):",
            "",
        ]
    )
    simulation_rows: list[tuple[str, str]] = [
        ("Mean rating", _fmt_pct(_dict(simulation.get("mean")).get("coverage_rate")))
    ]
    simulated_stars = _dict(simulation.get("stars"))
    for star in range(1, 6):
        simulation_rows.append(
            (
                f"{star}-star share",
                _fmt_pct(_dict(simulated_stars.get(str(star))).get("coverage_rate")),
            )
        )
    lines.extend(_table(["CI target", "Simulated coverage"], simulation_rows))
    lines.append("")
    return lines


def _periods(metrics: dict[str, Any], chart_links: dict[str, str]) -> list[str]:
    lines = ["## Rating periods", ""]
    periods = _list(metrics.get("periods"))
    if not periods:
        return lines + ["No dated period buckets were available.", ""]
    rows: list[tuple[str, ...]] = []
    for raw in periods:
        row = _dict(raw)
        rows.append(
            (
                str(row.get("label") or "period"),
                _fmt_int(row.get("n")),
                _fmt_float(_number(row.get("mean")), 2),
                _fmt_ci(_dict(row.get("mean_ci95")), decimals=2),
                _fmt_pct(row.get("one_two_star_share")),
            )
        )
    lines.extend(_table(["Period", "n", "Mean", "Mean 95% CI", "1-2 star share"], rows))
    lines.extend(["", f"![Rating by period]({chart_links['rating_by_period']})", ""])
    return lines


def _preprocessing(preprocessing: dict[str, Any]) -> list[str]:
    lines = ["## Preprocessing", ""]
    rows = [(label, _fmt_int(preprocessing.get(key))) for key, label in _PREPROCESSING_DENOMINATORS]
    lines.extend(_table(["Denominator", "Count"], rows))
    lines.append("")

    flags = _dict(preprocessing.get("flag_counts"))
    lines.extend(["### Preprocessing flags", ""])
    if flags:
        flag_rows = [(str(key), _fmt_int(value)) for key, value in sorted(flags.items())]
        lines.extend(_table(["Flag", "Count"], flag_rows))
    else:
        lines.append("No preprocessing flags were raised.")
    lines.append("")

    languages = _dict(preprocessing.get("language_counts"))
    lines.extend(["### Language counts", ""])
    if languages:
        language_rows = [(str(key), _fmt_int(value)) for key, value in sorted(languages.items())]
        lines.extend(_table(["Language", "Count"], language_rows))
    else:
        lines.append("No language classifications were recorded.")
    lines.append("")

    examples = _list(preprocessing.get("examples"))
    lines.extend(["### Before/after examples", ""])
    if examples:
        example_rows = [
            (
                _clip(str(_dict(example).get("before", "")), 260),
                _clip(str(_dict(example).get("after", "")), 260),
            )
            for example in examples[:5]
        ]
        lines.extend(_table(["Raw title + body", "analysis_text"], example_rows))
    else:
        lines.append("No preprocessing examples were available.")
    lines.append("")
    return lines


def _sentiment(
    sentiment: dict[str, Any], evaluation: dict[str, Any] | None, chart_links: dict[str, str]
) -> list[str]:
    lines = ["## Sentiment", ""]
    lines.append(f"Status: **{_safe(str(sentiment.get('status') or 'unknown'))}**.")
    lines.append("")
    distribution = _dict(sentiment.get("distribution"))
    if distribution:
        rows: list[tuple[str, ...]] = []
        for label in ("negative", "neutral", "positive"):
            row = _dict(distribution.get(label))
            rows.append(
                (
                    label,
                    _fmt_int(row.get("count")),
                    _fmt_pct(row.get("percentage")),
                    _fmt_ci(_dict(row.get("ci95")), percent=True),
                )
            )
        lines.extend(_table(["Label", "Count", "Share", "95% CI"], rows))
        lines.extend(
            [
                "",
                f"![Sentiment distribution]({chart_links['sentiment_distribution']})",
                "",
            ]
        )
    lines.extend(
        [
            f"Model: `{_safe(str(sentiment.get('model_id') or 'not recorded'))}`",
            "",
            f"Revision: `{_safe(str(sentiment.get('model_revision') or 'not recorded'))}`",
            "",
            SENTIMENT_MODEL_DOMAIN_CAVEAT,
            "",
        ]
    )
    consistency = _dict(sentiment.get("rating_consistency"))
    if consistency:
        lines.extend(["### Rating consistency diagnostic", ""])
        consistency_rows = [
            ("Comparable reviews", _fmt_int(consistency.get("comparable_reviews"))),
            ("Agreement rate", _fmt_pct(consistency.get("agreement_rate"))),
            (
                "Interpretation",
                _safe(str(consistency.get("note") or "Diagnostic only")),
            ),
        ]
        lines.extend(_table(["Metric", "Value"], consistency_rows))
        lines.append("")
    lines.extend(_evaluation_summary(evaluation))
    return lines


def _evaluation_summary(evaluation: dict[str, Any] | None) -> list[str]:
    lines = ["### Evaluation summary", ""]
    if evaluation is None:
        return lines + [
            "Sentiment benchmark: **evaluation not run**. Hand-labelled results are unavailable.",
            "",
            "Annotator repeatability: **evaluation not run**.",
            "",
            "Complaint-unit check: **evaluation not run**.",
            "",
            "Theme-threshold check: **evaluation not run**.",
            "",
            "Issue-category precision audit: **not audited**.",
            "",
        ]

    benchmark = _dict(evaluation.get("benchmark"))
    if benchmark.get("status") == "run":
        lines.extend(
            [
                f"Evaluable hand-labelled reviews: **{_fmt_int(benchmark.get('evaluable_n'))}**; "
                f"mixed excluded from 3-class metrics: **{_fmt_int(benchmark.get('mixed_n'))}**.",
                "",
            ]
        )
        model_rows: list[tuple[str, ...]] = []
        for raw_model in _list(benchmark.get("models")):
            model = _dict(raw_model)
            metrics = _dict(model.get("metrics"))
            model_rows.append(
                (
                    str(model.get("name") or "unknown"),
                    _fmt_float(_number(metrics.get("accuracy")), 3),
                    _fmt_float(_number(metrics.get("macro_f1")), 3),
                    _fmt_float(_number(metrics.get("negative_f1")), 3),
                )
            )
        if model_rows:
            lines.extend(
                _table(
                    ["Model", "Accuracy", "Macro-F1", "Negative F1"],
                    model_rows,
                )
            )
            lines.append("")
        paired = _dict(benchmark.get("paired_tabularis_minus_shipping_negative_f1_ci95"))
        if paired:
            lines.extend(
                [
                    "Tabularis minus shipping negative-F1 paired BCa 95% CI: "
                    f"**{_fmt_ci(paired, decimals=3)}**.",
                    "",
                ]
            )
        lines.extend(
            [
                _safe(str(benchmark.get("neutral_note") or "")),
                "",
                "Pre-written model decision rule: "
                + _safe(str(benchmark.get("decision_rule") or "not recorded")),
                "",
            ]
        )
    else:
        lines.extend(["Sentiment benchmark: **evaluation not run**.", ""])

    for key, label in (
        ("annotator_relabel", "Annotator repeatability"),
        ("complaint_units", "Complaint-unit check"),
        ("theme_threshold", "Theme-threshold check"),
    ):
        part = _dict(evaluation.get(key))
        if part.get("status") != "run":
            reason = str(part.get("reason") or "missing inputs")
            lines.extend([f"{label}: **evaluation not run** ({_safe(reason)}).", ""])
            continue
        if key == "annotator_relabel":
            lines.extend(
                [
                    "Annotator repeatability: kappa "
                    f"**{_fmt_float(_number(part.get('kappa')), 3)}** "
                    f"on **{_fmt_int(part.get('n'))}** repeated labels.",
                    "",
                    "The repeat-label file does not encode session timing; this measures "
                    "consistency of the supplied repeat labels and should be treated as an "
                    "independent later-session estimate only when that timing is documented.",
                    "",
                ]
            )
        elif key == "complaint_units":
            precision = _dict(part.get("precision"))
            recall = _dict(part.get("recall"))
            lines.extend(
                [
                    f"Complaint-unit check: precision **{_fmt_pct(precision.get('value'))}**, "
                    f"recall **{_fmt_pct(recall.get('value'))}**.",
                    "",
                ]
            )
        else:
            selected = part.get("selected_threshold")
            lines.extend(
                [
                    "Theme-threshold check: selected threshold "
                    + (
                        f"**{float(selected):.2f}**."
                        if isinstance(selected, (int, float))
                        else "**none met the precision target**."
                    ),
                    "",
                ]
            )
    category_audit = _dict(evaluation.get("issue_categories"))
    if category_audit.get("status") != "run":
        lines.extend(["Issue-category precision audit: **not audited**.", ""])
    else:
        audit_rows: list[tuple[str, ...]] = []
        for raw in _list(category_audit.get("categories")):
            row = _dict(raw)
            precision = _dict(row.get("precision"))
            audit_rows.append(
                (
                    str(row.get("category") or "unknown"),
                    _fmt_int(row.get("n")),
                    _fmt_pct(precision.get("value")),
                    _fmt_ci(precision, percent=True),
                )
            )
        lines.extend(["Issue-category precision audit:", ""])
        if audit_rows:
            lines.extend(_table(["Category", "Audited matches", "Precision", "95% CI"], audit_rows))
            lines.append("")
        else:
            lines.extend(["Audit marked run but contains no category rows.", ""])
    return lines


def _keywords(keywords: dict[str, Any]) -> list[str]:
    lines = ["## Keywords and phrases in negative reviews", ""]
    lines.append(f"Status: **{_safe(str(keywords.get('status') or 'unknown'))}**.")
    lines.append("")
    lines.extend(
        _phrase_table(
            "### A. Most common phrases",
            _list(keywords.get("common")),
            distinctive=False,
        )
    )
    lines.extend(
        _phrase_table(
            "### B. Most distinctive phrases",
            _list(keywords.get("distinctive")),
            distinctive=True,
        )
    )
    return lines


def _phrase_table(title: str, rows_raw: list[Any], *, distinctive: bool) -> list[str]:
    lines = [title, ""]
    rows: list[tuple[str, ...]] = []
    for raw in rows_raw[:15]:
        row = _dict(raw)
        negative = _dict(row.get("negative_reviews"))
        other = _dict(row.get("other_reviews"))
        negative_count = _integer(negative.get("count"))
        negative_total = _integer(negative.get("total"))
        share = negative_count / negative_total if negative_total else None
        base = (
            str(row.get("phrase") or ""),
            _support_text(negative),
            _fmt_pct_or_reason(share, "no negative reviews"),
            _support_text(other),
        )
        if distinctive:
            z = _number(row.get("z"))
            z_text = _fmt_float(z, 3)
            if bool(row.get("z_ge_1_96")):
                z_text += " ✓"
            rows.append((*base, z_text))
        else:
            rows.append(base)
    if not rows:
        return lines + ["No supported phrases.", ""]
    headers = ["Phrase", "Negative reviews", "Share of negative reviews", "Other reviews"]
    if distinctive:
        headers.append("Fightin' Words z")
    lines.extend(_table(headers, rows))
    lines.append("")
    if distinctive:
        lines.extend(
            [
                "Fightin' Words z is used as a ranking score, not as a significance test. "
                "A ✓ marks z >= 1.96.",
                "",
            ]
        )
    return lines


def _areas(
    insights: dict[str, Any],
    themes: dict[str, Any],
    evaluation: dict[str, Any] | None,
    chart_links: dict[str, str],
) -> list[str]:
    lines = ["## Areas of improvement", ""]
    lines.extend(
        [
            "Recurring complaints are grouped into fixed issue categories first; semantic "
            "clusters follow as a separate discovery layer.",
            "",
        ]
    )
    issue_categories = _dict(insights.get("issue_categories"))
    category_items = [
        item for item in _list(issue_categories.get("items")) if isinstance(item, dict)
    ]
    lines.extend(["### Issue categories", ""])
    lines.extend(
        [
            "Categories use a fixed, app-agnostic whole-token lexicon over complaint sentences. "
            "A review can match multiple categories, so category shares overlap.",
            "",
        ]
    )
    audit = _dict(evaluation.get("issue_categories")) if evaluation is not None else {}
    if audit.get("status") == "run":
        lines.extend(
            [
                "Category precision audit: **run**. Precision estimates are reported in the "
                "evaluation section and remain separate from sampling uncertainty.",
                "",
            ]
        )
    else:
        lines.extend(["Category precision audit: **not audited**.", ""])

    if category_items:
        rows: list[tuple[str, ...]] = []
        for item in category_items:
            evidence_ids = ", ".join(
                f"`{_safe(str(row.get('review_id')))}`"
                for row in _list(item.get("evidence"))
                if isinstance(row, dict) and row.get("review_id") is not None
            )
            rows.append(
                (
                    str(item.get("label") or item.get("category_id") or "Issue"),
                    f"{_integer(item.get('review_count'))} of {_integer(item.get('denominator'))}",
                    (
                        f"{_fmt_pct(item.get('share'))}; "
                        f"{_fmt_ci(_dict(item.get('ci95')), percent=True)}"
                    ),
                    _fmt_float(_number(item.get("mean_star_rating")), 2),
                    _fmt_pct(_dict(item.get("recency")).get("share_last_12_months")),
                    evidence_ids or "none",
                )
            )
        lines.extend(
            _table(
                [
                    "Category",
                    "Reviews",
                    "Share and 95% CI",
                    "Mean stars",
                    "Recent (last 12 months)",
                    "Evidence IDs",
                ],
                rows,
            )
        )
        lines.append("")
        for item in category_items:
            label = _safe(str(item.get("label") or "Issue category"))
            lines.extend([f"#### {label}", ""])
            lines.extend(
                [
                    _safe(str(item.get("description") or "")),
                    "",
                    "Suggested investigation: "
                    + _safe(
                        str(item.get("suggested_investigation") or "Check the matched evidence.")
                    ),
                    "",
                ]
            )
            phrases = [
                row for row in _list(item.get("top_matched_phrases")) if isinstance(row, dict)
            ]
            if phrases:
                lines.append(
                    "Top matched phrases: "
                    + ", ".join(
                        f"`{_safe(str(row.get('phrase') or ''))}` ({_integer(row.get('count'))})"
                        for row in phrases
                    )
                    + "."
                )
                lines.append("")
            evidence = [row for row in _list(item.get("evidence")) if isinstance(row, dict)]
            for row in evidence:
                lines.append(
                    f"- `{_safe(str(row.get('review_id') or 'unknown'))}`: "
                    f"“{_clip(str(row.get('excerpt') or ''), 300)}”"
                )
            lines.append("")
        lines.extend(
            [
                f"![Issue-category support]({chart_links['issue_category_support']})",
                "",
            ]
        )
    else:
        lines.extend(["No issue category met the two-review support threshold.", ""])

    not_categorised = _dict(issue_categories.get("not_categorised"))
    lines.extend(["### Not categorised", ""])
    nc_count = _integer(not_categorised.get("review_count"))
    nc_total = _integer(not_categorised.get("denominator"))
    lines.extend(
        [
            f"**{nc_count} of {nc_total} complaint reviews** "
            f"({_fmt_pct(not_categorised.get('share'))}) did not match a supported issue category.",
            "",
        ]
    )
    for row in [item for item in _list(not_categorised.get("evidence")) if isinstance(item, dict)]:
        lines.append(
            f"- `{_safe(str(row.get('review_id') or 'unknown'))}`: "
            f"“{_clip(str(row.get('excerpt') or ''), 300)}”"
        )
    lines.append("")
    lines.extend(["### Complaint sentence selection", ""])
    gate = _dict(themes.get("unit_gate"))
    removed = _integer(gate.get("removed_total"))
    candidates = _integer(gate.get("negative_label_candidates"))
    lines.extend(
        [
            "Complaint-unit score gate: "
            f"kept **{_fmt_int(gate.get('kept'))}** of **{candidates:,}** model-negative "
            f"sentence candidates and removed **{removed:,}** below the configured thresholds "
            f"({_fmt_float(_number(gate.get('positive_review_min_negative_score')), 2)} for "
            "4-5 star reviews; "
            f"{_fmt_float(_number(gate.get('other_review_min_negative_score')), 2)} otherwise).",
            "",
        ]
    )

    lines.extend(
        [
            "### Emerging clusters",
            "",
            "Semantic clusters remain a separate discovery layer; they are not merged into the "
            "fixed issue-category taxonomy.",
            "",
        ]
    )
    lines.extend(_coverage_summary(themes))
    cluster_areas = [
        item
        for item in _list(insights.get("areas_of_improvement"))
        if isinstance(item, dict) and item.get("source") in {"theme", "phrase"}
    ]
    if not cluster_areas:
        lines.extend(["No semantic cluster met the configured evidence threshold.", ""])
    else:
        for index, item in enumerate(cluster_areas, start=1):
            lines.append(
                f"#### Cluster {index}: {_safe(str(item.get('area') or 'Supported issue'))}"
            )
            lines.append("")
            lines.append(_safe(str(item.get("text") or "")))
            lines.append("")
            recency = _dict(item.get("recency"))
            evidence_ids = (
                ", ".join(
                    f"`{_safe(str(value))}`" for value in _list(item.get("evidence_review_ids"))
                )
                or "none"
            )
            lines.extend(
                [
                    f"- Evidence review IDs: {evidence_ids}",
                    f"- Newest evidence: {_safe(str(recency.get('newest_date') or 'unknown'))}",
                    f"- Share in last 12 months: {_fmt_pct(recency.get('share_last_12_months'))}",
                    f"- Historical-only: {bool(recency.get('historical', False))}",
                    "",
                ]
            )

    return lines


def _coverage_summary(themes: dict[str, Any]) -> list[str]:
    coverage = _dict(themes.get("coverage"))
    units_total = _integer(coverage.get("units_total"))
    units_clustered = _integer(coverage.get("units_clustered"))
    reviews_total = _integer(coverage.get("n_complaint_reviews"))
    reviews_clustered = _integer(coverage.get("complaint_reviews_in_themes"))
    unit_share = _number(coverage.get("unit_share"))
    review_share = _number(coverage.get("review_share"))
    if units_total == 0 and reviews_total == 0:
        return ["No complaint units were available for theme coverage.", ""]
    lines = [
        "Theme coverage: "
        f"**{units_clustered} of {units_total} complaint units ({_fmt_pct(unit_share)})** and "
        f"**{reviews_clustered} of {reviews_total} complaint reviews ({_fmt_pct(review_share)})** "
        "are represented in supported themes.",
        "",
    ]
    shares = [share for share in (unit_share, review_share) if share is not None]
    if shares and min(shares) < 0.50:
        lines.extend(
            [
                "**Theme coverage is below 50%.** The areas below describe only supported "
                "clusters; unclustered evidence remains in the `other` bucket and is not treated "
                "as a theme.",
                "",
            ]
        )
    return lines


def _category_audit_limitation(audit: dict[str, Any]) -> str:
    text = (
        f"Issue-category precision was measured on {_integer(audit.get('n'))} human-labelled "
        "category matches"
    )
    overall = _dict(audit.get("overall_precision"))
    if _number(overall.get("value")) is not None:
        text += (
            f": overall {_fmt_pct(overall.get('value'))} (95% CI {_fmt_ci(overall, percent=True)})"
        )
    text += "."
    weak: list[str] = []
    for raw in _list(audit.get("categories")):
        row = _dict(raw)
        value = _number(_dict(row.get("precision")).get("value"))
        if value is not None and value < _CATEGORY_PRECISION_TARGET:
            weak.append(
                f"{row.get('category', 'unknown')} {value:.1%} "
                f"({_integer(row.get('correct'))}/{_integer(row.get('n'))})"
            )
    if weak:
        text += (
            f" Categories below {_CATEGORY_PRECISION_TARGET:.0%} precision: {', '.join(weak)}; "
            "their shares may be overstated."
        )
    else:
        text += f" No audited category fell below {_CATEGORY_PRECISION_TARGET:.0%} precision."
    return text


def _limitations(
    population: dict[str, Any] | None,
    evaluation: dict[str, Any] | None,
    themes: dict[str, Any],
) -> list[str]:
    lines = ["## Limitations", ""]
    bullets = [
        "The sample covers written reviews in one storefront; star-only ratings are a "
        "different population.",
        "Confidence intervals quantify sampling uncertainty only; model and measurement "
        "error are separate.",
        "The Apple endpoint used for this demo is undocumented and may change without notice.",
        "Rank drift during live collection can cause duplicate/empty rows; the collector "
        "replaces them deterministically.",
        "Theme shares are conditional on the sentence classifier and clustering policy.",
        "The complaint-unit score thresholds are heuristics based on observed classifier "
        "errors and should be re-tuned on hand-labelled complaint units.",
    ]
    if population is None:
        bullets.append(
            "The population-walk aggregate was unavailable when this report was rendered."
        )
    if evaluation is None:
        bullets.append("Sentiment accuracy has not been measured on hand-labelled reviews.")
    else:
        benchmark = _dict(evaluation.get("benchmark"))
        if benchmark.get("status") != "run":
            bullets.append("The sentiment benchmark was not run.")
        relabel = _dict(evaluation.get("annotator_relabel"))
        if relabel.get("status") != "run":
            bullets.append("The later-session annotator repeatability check was not run.")
        else:
            bullets.append(
                "The repeat-label file does not encode session timing, so its kappa measures "
                "repeat-label consistency but does not by itself prove independent later-session "
                "repeatability."
            )
        unit_check = _dict(evaluation.get("complaint_units"))
        if unit_check.get("status") != "run":
            bullets.append("The complaint-unit precision/recall check was not run.")
        else:
            subgroups = _dict(unit_check.get("subgroups"))
            for name, label in (
                ("mixed_reviews", "mixed reviews"),
                ("rating_4_5", "4-5 star reviews"),
            ):
                subgroup = _dict(subgroups.get(name))
                recall = _number(subgroup.get("value"))
                if recall is not None and recall < 0.80:
                    bullets.append(
                        f"Complaint-unit recall is low on {label} ({recall:.1%}); "
                        "theme extraction may miss embedded complaints."
                    )
        threshold = _dict(evaluation.get("theme_threshold"))
        if threshold.get("status") != "run":
            bullets.append("The labelled-pair theme-threshold check was not run.")
        elif threshold.get("selected_threshold") is None:
            bullets.append(
                "No evaluated theme distance threshold met the 0.80 same-issue precision target."
            )
    bullets.extend(
        [
            "Issue categories are lexicon-based heuristics with a fixed generic vocabulary; they "
            "are not learned from this app and can miss paraphrases or ambiguous uses.",
            "Issue-category shares are multi-label and may overlap; they must not be summed to "
            "100%.",
        ]
    )
    category_audit = _dict(evaluation.get("issue_categories")) if evaluation is not None else {}
    if category_audit.get("status") != "run":
        bullets.append(
            "Issue-category precision has not been measured; the optional human audit has not "
            "been run."
        )
    else:
        bullets.append(_category_audit_limitation(category_audit))
    coverage = _dict(themes.get("coverage"))
    coverage_values = [
        _number(coverage.get("unit_share")),
        _number(coverage.get("review_share")),
    ]
    if any(value is not None and value < 0.50 for value in coverage_values):
        bullets.append(
            "Fewer than half of complaint units or complaint reviews are represented in "
            "supported themes, so theme conclusions are necessarily partial."
        )
    lines.extend([f"- {item}" for item in bullets])
    lines.append("")
    return lines


def _methodology(themes: dict[str, Any]) -> list[str]:
    threshold = _fmt_float(_number(themes.get("distance_threshold")), 2)
    return [
        "## Methodology",
        "",
        f"- Sampling: {SAMPLING_METHOD}.",
        f"- Uncertainty: {RATING_INTERVAL}.",
        "- Rating metrics use every sampled review; model-derived statistics use "
        "analysable reviews only.",
        "- Common 1-3 grams and contrastive Fightin' Words rankings are calculated from "
        "negative reviews. Displayed phrases must contain at least one content token.",
        "- Issue categories use a versioned, generic whole-token lexicon over score-gated "
        "complaint sentences; category support is review-level and multi-label.",
        "- Complaint themes are built from score-gated negative sentences with pinned MiniLM "
        f"embeddings and agglomerative clustering at cosine distance threshold {threshold}.",
        "",
    ]


def _reproduce() -> list[str]:
    return [
        "## How to reproduce",
        "",
        "```powershell",
        "uv run reviews analyze --provider fixture `",
        "  --snapshot data/fixtures/nebula_us_seed42.snapshot.json `",
        "  --out reports/nebula_us_seed42.analysis.json",
        "uv run reviews report",
        "```",
        "",
        REPRODUCTION_NOTE,
        "",
    ]


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    escaped_headers = [_safe(value) for value in headers]
    output = [
        "| " + " | ".join(escaped_headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        output.append("| " + " | ".join(_safe(str(value)) for value in row) + " |")
    return output


def _support_text(value: dict[str, Any]) -> str:
    count = _integer(value.get("count"))
    total = _integer(value.get("total"))
    return f"{count} of {total}"


def _coverage_word(value: bool | None) -> str:
    if value is None:
        return "not available (CI unavailable)"
    return "yes" if value else "no"


def _fmt_pct_or_reason(value: float | None, reason: str) -> str:
    return f"not available ({reason})" if value is None else f"{value:.1%}"


def _safe(value: str) -> str:
    return value.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _clip(value: str, limit: int) -> str:
    safe = _safe(value)
    return safe if len(safe) <= limit else safe[: limit - 1].rstrip() + "…"


def _relative_markdown_path(path: Path, base: Path) -> str:
    try:
        relative = path.relative_to(base)
    except ValueError:
        relative = path
    return relative.as_posix()


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _integer(value: object) -> int:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else 0


def _fmt_float(value: float | None, decimals: int) -> str:
    return "not available" if value is None else f"{value:.{decimals}f}"


def _fmt_int(value: object) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        return f"{value:,}"
    return "not available"


def _fmt_pct(value: object) -> str:
    number = _number(value)
    return "not available" if number is None else f"{number:.1%}"


def _fmt_ci(ci: dict[str, Any], *, decimals: int = 3, percent: bool = False) -> str:
    low = _number(ci.get("low"))
    high = _number(ci.get("high"))
    if low is None or high is None:
        reason = ci.get("reason")
        return f"not available ({reason})" if reason else "not available (CI unavailable)"
    if percent:
        return f"{low:.1%} to {high:.1%}"
    return f"{low:.{decimals}f} to {high:.{decimals}f}"
