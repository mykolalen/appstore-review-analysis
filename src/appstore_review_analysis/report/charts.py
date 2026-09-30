"""Chart-data extraction and deterministic PNG rendering for the demo report."""

from __future__ import annotations

import textwrap
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypedDict

import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from matplotlib.patches import FancyBboxPatch
from matplotlib.ticker import PercentFormatter

plt.switch_backend("Agg")

CATEGORY_PRECISION_TARGET = 0.80

_INK = "#1f2937"
_MUTED = "#6b7280"
_GRID = "#e5e7eb"
_AXIS = "#cbd5e1"
_ACCENT = "#6d5ae6"
_TILE_FILL = "#f5f3ff"
_TILE_EDGE = "#ddd8fb"
_NEGATIVE = "#d6455d"
_NEUTRAL = "#9aa3b2"
_POSITIVE = "#2f9e77"
_WARNING = "#f2a23a"
_STAR_COLORS = ["#d6455d", "#ef8a5a", "#f2c14e", "#8cc084", "#2f9e77"]
_FUNNEL_COLORS = ["#cfc9f7", "#9b8df0", "#6d5ae6"]
_TOP_PHRASES = 12


class ChartData(TypedDict, total=False):
    """JSON-like values used both by tests and by the PNG renderers."""

    labels: list[str]
    values: list[float]
    low: list[float]
    high: list[float]
    counts: list[int]
    correct: list[int]
    total: int


class SummaryTile(TypedDict):
    value: str
    label: str
    detail: str


class SummaryCard(TypedDict):
    title: str
    subtitle: str
    tiles: list[SummaryTile]


def rating_distribution_data(analysis: dict[str, Any]) -> ChartData:
    distribution = _dict(_dict(analysis.get("metrics")).get("distribution"))
    labels: list[str] = []
    values: list[float] = []
    lows: list[float] = []
    highs: list[float] = []
    counts: list[int] = []
    for star in range(1, 6):
        row = _dict(distribution.get(str(star)))
        percentage = _number(row.get("percentage"), default=0.0)
        ci = _dict(row.get("ci95"))
        labels.append(str(star))
        values.append(percentage)
        lows.append(_number(ci.get("low"), default=percentage))
        highs.append(_number(ci.get("high"), default=percentage))
        counts.append(_integer(row.get("count")))
    return {
        "labels": labels,
        "values": values,
        "low": lows,
        "high": highs,
        "counts": counts,
    }


def sentiment_distribution_data(analysis: dict[str, Any]) -> ChartData:
    distribution = _dict(_dict(analysis.get("sentiment")).get("distribution"))
    labels: list[str] = []
    values: list[float] = []
    lows: list[float] = []
    highs: list[float] = []
    counts: list[int] = []
    for label in ("negative", "neutral", "positive"):
        row = _dict(distribution.get(label))
        percentage = _number(row.get("percentage"), default=0.0)
        ci = _dict(row.get("ci95"))
        labels.append(label)
        values.append(percentage)
        lows.append(_number(ci.get("low"), default=percentage))
        highs.append(_number(ci.get("high"), default=percentage))
        counts.append(_integer(row.get("count")))
    return {
        "labels": labels,
        "values": values,
        "low": lows,
        "high": highs,
        "counts": counts,
    }


def issue_category_support_data(analysis: dict[str, Any]) -> ChartData:
    issue_categories = _dict(_dict(analysis.get("insights")).get("issue_categories"))
    items = _list(issue_categories.get("items"))
    labels: list[str] = []
    values: list[float] = []
    lows: list[float] = []
    highs: list[float] = []
    counts: list[int] = []
    for raw in items:
        item = _dict(raw)
        share = _number(item.get("share"), default=0.0)
        ci = _dict(item.get("ci95"))
        labels.append(str(item.get("label") or item.get("category_id") or "issue")[:48])
        values.append(share)
        lows.append(_number(ci.get("low"), default=share))
        highs.append(_number(ci.get("high"), default=share))
        counts.append(_integer(item.get("review_count")))
    return {
        "labels": labels,
        "values": values,
        "low": lows,
        "high": highs,
        "counts": counts,
        "total": _integer(issue_categories.get("denominator")),
    }


def period_rating_data(analysis: dict[str, Any]) -> ChartData:
    periods = _list(_dict(analysis.get("metrics")).get("periods"))
    labels: list[str] = []
    values: list[float] = []
    lows: list[float] = []
    highs: list[float] = []
    counts: list[int] = []
    for raw in periods:
        row = _dict(raw)
        mean = _number(row.get("mean"), default=0.0)
        ci = _dict(row.get("mean_ci95"))
        labels.append(str(row.get("label", "period")))
        values.append(mean)
        lows.append(_number(ci.get("low"), default=mean))
        highs.append(_number(ci.get("high"), default=mean))
        counts.append(_integer(row.get("n")))
    return {
        "labels": labels,
        "values": values,
        "low": lows,
        "high": highs,
        "counts": counts,
    }


def negative_phrases_data(analysis: dict[str, Any]) -> ChartData:
    """Most common negative-review phrases, as the share of model-negative reviews."""

    keywords = _dict(analysis.get("keywords"))
    total = _integer(keywords.get("n_negative"))
    labels: list[str] = []
    values: list[float] = []
    counts: list[int] = []
    for raw in _list(keywords.get("common"))[:_TOP_PHRASES]:
        row = _dict(raw)
        phrase = str(row.get("phrase") or "").strip()
        count = _integer(row.get("support_count"))
        if not phrase or count < 1:
            continue
        labels.append(phrase)
        counts.append(count)
        values.append(count / total if total > 0 else 0.0)
    return {"labels": labels, "values": values, "counts": counts, "total": total}


def complaint_funnel_data(analysis: dict[str, Any]) -> ChartData:
    """Sampled reviews -> reviews with complaint sentences -> reviews in a named category."""

    preprocessing = _dict(analysis.get("preprocessing"))
    issue_categories = _dict(_dict(analysis.get("insights")).get("issue_categories"))
    sampled = _integer(preprocessing.get("n_all"))
    complaint_reviews = _integer(preprocessing.get("n_complaint_reviews"))
    uncategorised = _integer(_dict(issue_categories.get("not_categorised")).get("review_count"))
    categorised = max(0, complaint_reviews - uncategorised)
    return {
        "labels": [
            "Sampled reviews",
            "Reviews with complaint sentences",
            "Reviews in a named issue category",
        ],
        "counts": [sampled, complaint_reviews, categorised],
        "values": [
            count / sampled if sampled > 0 else 0.0
            for count in (sampled, complaint_reviews, categorised)
        ],
        "total": sampled,
    }


def category_precision_data(
    evaluation: dict[str, Any] | None,
    names: dict[str, str] | None = None,
) -> ChartData | None:
    """Per-category precision from the human audit; ``None`` when no audit was run."""

    audit = _dict(_dict(evaluation).get("issue_categories"))
    if audit.get("status") != "run":
        return None
    rows: list[tuple[float, str, float, float, int, int]] = []
    for raw in _list(audit.get("categories")):
        row = _dict(raw)
        precision = _dict(row.get("precision"))
        value = _number(precision.get("value"), default=-1.0)
        n = _integer(row.get("n"))
        if value < 0.0 or n < 1:
            continue
        category = str(row.get("category") or "unknown")
        label = (names or {}).get(category) or category.replace("_", " ").capitalize()
        rows.append(
            (
                value,
                label,
                _number(precision.get("low"), default=value),
                _number(precision.get("high"), default=value),
                n,
                _integer(row.get("correct")),
            )
        )
    if not rows:
        return None
    rows.sort(key=lambda item: (-item[0], -item[4], item[1]))
    return {
        "labels": [row[1] for row in rows],
        "values": [row[0] for row in rows],
        "low": [row[2] for row in rows],
        "high": [row[3] for row in rows],
        "counts": [row[4] for row in rows],
        "correct": [row[5] for row in rows],
        "total": sum(row[4] for row in rows),
    }


def summary_card_data(analysis: dict[str, Any]) -> SummaryCard:
    """Headline numbers for a one-glance opening slide."""

    app = _dict(analysis.get("app"))
    sampling = _dict(analysis.get("sampling"))
    metrics = _dict(analysis.get("metrics"))
    negative = _dict(_dict(_dict(analysis.get("sentiment")).get("distribution")).get("negative"))
    tiles: list[SummaryTile] = []

    n = _integer(metrics.get("n"))
    reachable = _integer(sampling.get("reachable"))
    if n > 0:
        tiles.append(
            {
                "value": f"{n:,}",
                "label": "sampled written reviews",
                "detail": f"of {reachable:,} reachable" if reachable else "",
            }
        )
    mean = _number(metrics.get("mean"), default=-1.0)
    if mean >= 0.0:
        ci = _dict(metrics.get("mean_ci95"))
        low = _number(ci.get("low"), default=-1.0)
        high = _number(ci.get("high"), default=-1.0)
        tiles.append(
            {
                "value": f"{mean:.2f}",
                "label": "mean rating (out of 5)",
                "detail": f"95% CI {low:.2f} to {high:.2f}" if low >= 0.0 and high >= 0.0 else "",
            }
        )
    share = _number(negative.get("percentage"), default=-1.0)
    if share >= 0.0:
        ci = _dict(negative.get("ci95"))
        low = _number(ci.get("low"), default=-1.0)
        high = _number(ci.get("high"), default=-1.0)
        tiles.append(
            {
                "value": f"{share:.0%}",
                "label": "negative sentiment",
                "detail": f"95% CI {low:.1%} to {high:.1%}" if low >= 0.0 and high >= 0.0 else "",
            }
        )
    for raw in _list(_dict(analysis.get("insights")).get("areas_of_improvement")):
        area = _dict(raw)
        if area.get("source") != "issue_category":
            continue
        complaint = _dict(area.get("complaint_reviews"))
        area_share = _number(complaint.get("share"), default=-1.0)
        if area_share < 0.0:
            break
        tiles.append(
            {
                "value": f"{area_share:.0%}",
                "label": f"top issue: {area.get('area') or 'issue category'}",
                "detail": (
                    f"{_integer(complaint.get('count'))} of "
                    f"{_integer(complaint.get('total'))} complaint reviews"
                ),
            }
        )
        break
    storefront = str(sampling.get("storefront") or "").upper()
    seed = sampling.get("seed")
    context = [part for part in ("App Store review analysis", storefront) if part]
    if isinstance(seed, int) and not isinstance(seed, bool):
        context.append(f"seed {seed}")
    return {
        "title": str(app.get("name") or "App Store app"),
        "subtitle": " · ".join(context),
        "tiles": tiles,
    }


def render_all_charts(
    analysis: dict[str, Any],
    charts_dir: Path,
    evaluation: dict[str, Any] | None = None,
) -> dict[str, Path]:
    """Render every report chart and return stable logical names -> paths.

    The category-precision chart is rendered only when a human category audit was run.
    """

    charts_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "summary_card": charts_dir / "summary_card.png",
        "rating_distribution": charts_dir / "rating_distribution.png",
        "sentiment_distribution": charts_dir / "sentiment_distribution.png",
        "issue_category_support": charts_dir / "issue_category_support.png",
        "rating_by_period": charts_dir / "rating_by_period.png",
        "negative_phrases": charts_dir / "negative_phrases.png",
        "complaint_funnel": charts_dir / "complaint_funnel.png",
    }
    with _style():
        _plot_summary_card(summary_card_data(analysis), outputs["summary_card"])
        _plot_proportion_bars(
            rating_distribution_data(analysis),
            outputs["rating_distribution"],
            title="Rating distribution",
            subject="sampled reviews",
            tick_labels=[f"{star}★" for star in range(1, 6)],
            colors=_STAR_COLORS,
        )
        _plot_proportion_bars(
            sentiment_distribution_data(analysis),
            outputs["sentiment_distribution"],
            title="Sentiment distribution",
            subject="analysable reviews",
            tick_labels=["Negative", "Neutral", "Positive"],
            colors=[_NEGATIVE, _NEUTRAL, _POSITIVE],
        )
        _plot_issue_category_support(
            issue_category_support_data(analysis), outputs["issue_category_support"]
        )
        _plot_periods(period_rating_data(analysis), outputs["rating_by_period"])
        _plot_negative_phrases(negative_phrases_data(analysis), outputs["negative_phrases"])
        _plot_complaint_funnel(complaint_funnel_data(analysis), outputs["complaint_funnel"])
        legacy_theme_chart = charts_dir / "theme_support.png"
        legacy_theme_chart.unlink(missing_ok=True)
        precision = category_precision_data(evaluation, _category_names(analysis))
        if precision is not None:
            outputs["category_precision"] = charts_dir / "category_precision.png"
            _plot_category_precision(precision, outputs["category_precision"])
    return outputs


@contextmanager
def _style() -> Iterator[None]:
    with plt.rc_context(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "text.color": _INK,
            "axes.edgecolor": _AXIS,
            "axes.labelcolor": _MUTED,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "axes.axisbelow": True,
            "grid.color": _GRID,
            "grid.linewidth": 0.8,
            "xtick.color": _MUTED,
            "ytick.color": _INK,
            "figure.facecolor": "white",
            "savefig.facecolor": "white",
        }
    ):
        yield


_HEADER_INCHES = 0.95


def _headline(fig: Any, title: str, subtitle: str) -> None:
    """Left-aligned title block at the figure edge, with a fixed physical height."""

    height = fig.get_figheight()
    fig.text(0.03, 1 - 0.16 / height, title, fontsize=14, fontweight="bold", color=_INK, va="top")
    fig.text(0.03, 1 - 0.54 / height, subtitle, fontsize=9, color=_MUTED, va="top")


def _save(fig: Any, path: Path) -> None:
    fig.tight_layout(rect=(0.0, 0.0, 1.0, 1 - _HEADER_INCHES / fig.get_figheight()))
    fig.savefig(path, dpi=160)
    plt.close(fig)


def _errors(values: list[float], lows: list[float], highs: list[float]) -> list[list[float]]:
    return [
        [max(0.0, value - low) for value, low in zip(values, lows, strict=True)],
        [max(0.0, high - value) for value, high in zip(values, highs, strict=True)],
    ]


def _plot_proportion_bars(
    data: ChartData,
    path: Path,
    *,
    title: str,
    subject: str,
    tick_labels: list[str],
    colors: list[str],
) -> None:
    values = data["values"]
    lows = data["low"]
    highs = data["high"]
    counts = data["counts"]
    positions = list(range(len(values)))
    top = max(highs, default=1.0) or 1.0
    fig, ax = plt.subplots(figsize=(7.4, 4.6))
    ax.bar(positions, values, width=0.62, color=colors[: len(values)], zorder=2)
    ax.errorbar(
        positions,
        values,
        yerr=_errors(values, lows, highs),
        fmt="none",
        ecolor=_INK,
        elinewidth=1.2,
        capsize=4,
        zorder=3,
    )
    for position, value, high in zip(positions, values, highs, strict=True):
        ax.text(
            position,
            high + top * 0.03,
            f"{value:.0%}",
            ha="center",
            va="bottom",
            fontsize=11,
            fontweight="bold",
        )
    ax.set_xticks(
        positions, [f"{label}\nn={count}" for label, count in zip(tick_labels, counts, strict=True)]
    )
    ax.set_ylim(0.0, top * 1.2)
    ax.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    ax.grid(axis="x", visible=False)
    ax.tick_params(axis="x", length=0)
    _headline(fig, title, f"Share of {sum(counts)} {subject} · whiskers show the 95% CI")
    _save(fig, path)


def _plot_horizontal_shares(
    ax: Axes,
    labels: list[str],
    values: list[float],
    lows: list[float],
    highs: list[float],
    notes: list[str],
    colors: list[str],
    *,
    whiskers: bool = True,
) -> None:
    positions = list(range(len(labels)))
    top = max(highs, default=1.0) or 1.0
    ax.barh(positions, values, height=0.62, color=colors, zorder=2)
    if whiskers:
        ax.errorbar(
            values,
            positions,
            xerr=_errors(values, lows, highs),
            fmt="none",
            ecolor=_INK,
            elinewidth=1.1,
            capsize=3,
            zorder=3,
        )
    for position, high, note in zip(positions, highs, notes, strict=True):
        ax.text(
            high + top * 0.02,
            position,
            note,
            va="center",
            fontsize=9.5,
            color=_INK,
            zorder=4,
            bbox={"facecolor": "white", "edgecolor": "none", "pad": 1.5, "alpha": 0.85},
        )
    ax.set_yticks(positions, labels)
    ax.invert_yaxis()
    ax.set_xlim(0.0, top * 1.42)
    ax.xaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    ax.grid(axis="y", visible=False)
    ax.tick_params(axis="y", length=0)


def _plot_issue_category_support(data: ChartData, path: Path) -> None:
    labels = data["labels"]
    values = data["values"]
    counts = data["counts"]
    total = data.get("total", 0)
    notes = [
        f"{value:.1%} ({count} of {total})" if total else f"{value:.1%} ({count})"
        for value, count in zip(values, counts, strict=True)
    ]
    fig, ax = plt.subplots(figsize=(8.8, max(4.2, 0.56 * max(1, len(labels)) + 2.2)))
    _plot_horizontal_shares(
        ax, labels, values, data["low"], data["high"], notes, [_ACCENT] * len(labels)
    )
    _headline(
        fig,
        "Issue-category support",
        f"Share of {total} complaint reviews · categories overlap, so shares do not add up"
        if total
        else "Share of complaint reviews · categories overlap, so shares do not add up",
    )
    _save(fig, path)


def _plot_periods(data: ChartData, path: Path) -> None:
    # Periods arrive newest first; plot oldest to newest so the line reads as a trend.
    labels = data["labels"][::-1]
    values = data["values"][::-1]
    lows = data["low"][::-1]
    highs = data["high"][::-1]
    counts = data["counts"][::-1]
    positions = list(range(len(labels)))
    fig, ax = plt.subplots(figsize=(7.4, 4.6))
    if positions:
        ax.fill_between(positions, lows, highs, color=_ACCENT, alpha=0.15, linewidth=0, zorder=1)
        ax.plot(positions, values, marker="o", color=_ACCENT, linewidth=2.4, markersize=8, zorder=3)
        for position, value in zip(positions, values, strict=True):
            ax.text(
                position,
                value + 0.16,
                f"{value:.2f}",
                ha="center",
                va="bottom",
                fontsize=11,
                fontweight="bold",
            )
    ax.set_xticks(
        positions, [f"{label}\nn={count}" for label, count in zip(labels, counts, strict=True)]
    )
    ax.set_ylim(1.0, 5.3)
    ax.set_yticks([1, 2, 3, 4, 5])
    ax.set_xlim(-0.4, max(0.4, len(positions) - 0.6))
    ax.grid(axis="x", visible=False)
    ax.tick_params(axis="x", length=0)
    _headline(
        fig,
        "Rating by period",
        "Mean star rating per review period, oldest to newest · band shows the 95% CI",
    )
    _save(fig, path)


def _plot_negative_phrases(data: ChartData, path: Path) -> None:
    labels = data["labels"]
    values = data["values"]
    counts = data["counts"]
    total = data.get("total", 0)
    notes = [f"{count} of {total}" if total else str(count) for count in counts]
    fig, ax = plt.subplots(figsize=(8.0, max(3.6, 0.42 * max(1, len(labels)) + 2.0)))
    _plot_horizontal_shares(
        ax, labels, values, values, values, notes, [_NEGATIVE] * len(labels), whiskers=False
    )
    _headline(
        fig,
        "Most common phrases in negative reviews",
        f"Share of {total} model-negative reviews containing the phrase · "
        "overlapping phrases are not additive"
        if total
        else "Share of model-negative reviews containing the phrase",
    )
    _save(fig, path)


def _plot_complaint_funnel(data: ChartData, path: Path) -> None:
    labels = data["labels"]
    counts = data["counts"]
    first = max(counts[0], 1) if counts else 1
    positions = list(range(len(counts)))
    fig, ax = plt.subplots(figsize=(8.4, 3.9))
    lefts = [(first - count) / 2 for count in counts]
    ax.barh(positions, counts, left=lefts, height=0.68, color=_FUNNEL_COLORS[: len(counts)])
    for position, count in zip(positions, counts, strict=True):
        ax.text(
            first / 2,
            position,
            f"{count}" if position == 0 else f"{count}  ({count / first:.0%})",
            ha="center",
            va="center",
            fontsize=13,
            fontweight="bold",
            color="white" if position > 0 else _INK,
        )
    ax.set_yticks(positions, labels)
    ax.invert_yaxis()
    ax.set_xlim(-first * 0.02, first * 1.02)
    ax.set_xticks([])
    ax.grid(visible=False)
    ax.tick_params(axis="y", length=0)
    ax.spines["left"].set_visible(False)
    ax.spines["bottom"].set_visible(False)
    uncategorised = counts[1] - counts[2] if len(counts) == 3 else 0
    note = (
        f"{uncategorised} complaint reviews fit no category and are listed separately"
        if uncategorised > 0
        else "Every complaint review fits at least one category"
    )
    _headline(fig, "From sampled reviews to categorised complaints", note)
    _save(fig, path)


def _plot_category_precision(data: ChartData, path: Path) -> None:
    labels = data["labels"]
    values = data["values"]
    counts = data["counts"]
    correct = data["correct"]
    total = data.get("total", sum(counts))
    overall = sum(correct) / total if total else 0.0
    colors = [_POSITIVE if value >= CATEGORY_PRECISION_TARGET else _WARNING for value in values]
    notes = [
        f"{value:.0%} ({hits}/{n})" for value, hits, n in zip(values, correct, counts, strict=True)
    ]
    fig, ax = plt.subplots(figsize=(8.8, max(4.2, 0.56 * max(1, len(labels)) + 2.2)))
    _plot_horizontal_shares(ax, labels, values, data["low"], data["high"], notes, colors)
    ax.set_xlim(0.0, 1.45)
    ax.set_xticks([0.0, 0.2, 0.4, 0.6, 0.8, 1.0])
    ax.axvline(CATEGORY_PRECISION_TARGET, color=_INK, linestyle="--", linewidth=1.0, zorder=1)
    _headline(
        fig,
        "Issue-category precision (human audit)",
        f"{sum(correct)} of {total} category matches judged correct ({overall:.1%}) · "
        f"dashed line = {CATEGORY_PRECISION_TARGET:.0%} target · whiskers show the 95% CI",
    )
    _save(fig, path)


def _plot_summary_card(card: SummaryCard, path: Path) -> None:
    tiles = card["tiles"]
    fig = plt.figure(figsize=(10.0, 3.6))
    ax = fig.add_axes((0.0, 0.0, 1.0, 1.0))
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 36)
    ax.axis("off")
    ax.text(3, 33, card["title"], fontsize=18, fontweight="bold", va="top", color=_INK)
    ax.text(3, 27.6, card["subtitle"], fontsize=10, va="top", color=_MUTED)
    if tiles:
        gap = 2.0
        left = 3.0
        width = (100 - 2 * left - gap * (len(tiles) - 1)) / len(tiles)
        for index, tile in enumerate(tiles):
            x = left + index * (width + gap)
            ax.add_patch(
                FancyBboxPatch(
                    (x, 2.0),
                    width,
                    20.0,
                    boxstyle="round,pad=0,rounding_size=1.6",
                    facecolor=_TILE_FILL,
                    edgecolor=_TILE_EDGE,
                    linewidth=1.2,
                )
            )
            centre = x + width / 2
            ax.text(
                centre,
                19.6,
                tile["value"],
                ha="center",
                va="top",
                fontsize=26,
                fontweight="bold",
                color=_ACCENT,
            )
            ax.text(
                centre,
                12.0,
                textwrap.fill(tile["label"], 26),
                ha="center",
                va="top",
                fontsize=9.5,
                color=_INK,
            )
            ax.text(
                centre,
                4.6,
                tile["detail"],
                ha="center",
                va="bottom",
                fontsize=8.5,
                color=_MUTED,
            )
    fig.savefig(path, dpi=160)
    plt.close(fig)


def _category_names(analysis: dict[str, Any]) -> dict[str, str]:
    issue_categories = _dict(_dict(analysis.get("insights")).get("issue_categories"))
    names: dict[str, str] = {}
    for raw in _list(issue_categories.get("items")):
        item = _dict(raw)
        category_id = item.get("category_id")
        label = item.get("label")
        if isinstance(category_id, str) and isinstance(label, str):
            names[category_id] = label
    return names


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _number(value: object, *, default: float) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return default


def _integer(value: object) -> int:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else 0
