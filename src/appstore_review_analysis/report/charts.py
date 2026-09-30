"""Chart-data extraction and deterministic PNG rendering for the demo report."""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict

import matplotlib.pyplot as plt

plt.switch_backend("Agg")


class ChartData(TypedDict, total=False):
    """JSON-like values used both by tests and by the PNG renderers."""

    labels: list[str]
    values: list[float]
    low: list[float]
    high: list[float]
    counts: list[int]


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


def theme_support_data(analysis: dict[str, Any]) -> ChartData:
    items = _list(_dict(analysis.get("themes")).get("items"))
    labels: list[str] = []
    values: list[float] = []
    counts: list[int] = []
    for index, raw in enumerate(items, start=1):
        item = _dict(raw)
        phrases = _list(item.get("top_phrases"))
        first = _dict(phrases[0]).get("phrase") if phrases else None
        label = str(first or item.get("theme_id") or f"theme {index}")[:48]
        labels.append(label)
        share = _dict(item.get("share_of_complaint_reviews"))
        values.append(_number(share.get("value"), default=0.0))
        counts.append(_integer(item.get("review_count")))
    return {"labels": labels, "values": values, "counts": counts}


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


def render_all_charts(analysis: dict[str, Any], charts_dir: Path) -> dict[str, Path]:
    """Render all four required charts and return stable logical names -> paths."""

    charts_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "rating_distribution": charts_dir / "rating_distribution.png",
        "sentiment_distribution": charts_dir / "sentiment_distribution.png",
        "theme_support": charts_dir / "theme_support.png",
        "rating_by_period": charts_dir / "rating_by_period.png",
    }
    _plot_proportion_bars(
        rating_distribution_data(analysis),
        outputs["rating_distribution"],
        title="Rating distribution",
        ylabel="Share of sampled reviews",
    )
    _plot_proportion_bars(
        sentiment_distribution_data(analysis),
        outputs["sentiment_distribution"],
        title="Sentiment distribution",
        ylabel="Share of analysable reviews",
    )
    _plot_theme_support(theme_support_data(analysis), outputs["theme_support"])
    _plot_periods(period_rating_data(analysis), outputs["rating_by_period"])
    return outputs


def _plot_proportion_bars(
    data: ChartData,
    path: Path,
    *,
    title: str,
    ylabel: str,
) -> None:
    labels = data["labels"]
    values = data["values"]
    lows = data["low"]
    highs = data["high"]
    lower_errors = [max(0.0, value - low) for value, low in zip(values, lows, strict=True)]
    upper_errors = [max(0.0, high - value) for value, high in zip(values, highs, strict=True)]
    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    positions = list(range(len(labels)))
    ax.bar(positions, values, yerr=[lower_errors, upper_errors], capsize=4)
    ax.set_xticks(positions, labels)
    ax.set_ylim(0.0, max(1.0, max(highs, default=1.0) * 1.08))
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def _plot_theme_support(data: ChartData, path: Path) -> None:
    labels = data["labels"]
    values = data["values"]
    fig, ax = plt.subplots(figsize=(8.0, max(3.8, 0.55 * max(1, len(labels)) + 1.8)))
    positions = list(range(len(labels)))
    ax.barh(positions, values)
    ax.set_yticks(positions, labels)
    ax.invert_yaxis()
    ax.set_xlim(0.0, max(1.0, max(values, default=1.0) * 1.08))
    ax.set_xlabel("Share of complaint reviews")
    ax.set_title("Complaint theme support")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def _plot_periods(data: ChartData, path: Path) -> None:
    labels = data["labels"]
    values = data["values"]
    lows = data["low"]
    highs = data["high"]
    lower_errors = [max(0.0, value - low) for value, low in zip(values, lows, strict=True)]
    upper_errors = [max(0.0, high - value) for value, high in zip(values, highs, strict=True)]
    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    positions = list(range(len(labels)))
    if positions:
        ax.errorbar(
            positions,
            values,
            yerr=[lower_errors, upper_errors],
            marker="o",
            capsize=4,
        )
    ax.set_xticks(positions, labels)
    ax.set_ylim(1.0, 5.0)
    ax.set_ylabel("Mean rating")
    ax.set_title("Rating by period")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


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
