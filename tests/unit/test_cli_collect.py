from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from appstore_review_analysis.cli import app
from appstore_review_analysis.config import get_settings
from appstore_review_analysis.domain import AppInfo, CollectionResult, Review, SamplingMetadata

runner = CliRunner()


def _write_snapshot(directory: Path) -> None:
    reviews = [
        Review(
            source_review_id=f"r-{index}",
            rank=index,
            country="us",
            title=f"Title {index}",
            body=f"Body {index}",
            rating=(index % 5) + 1,
            created_at=datetime(2026, 9, 1, tzinfo=UTC),
            is_edited=False,
            vote_count=0,
            vote_sum=0,
            has_developer_response=False,
            developer_response_id=None,
            developer_response_date=None,
            provider="itunes",
        )
        for index in range(100)
    ]
    snapshot = CollectionResult(
        app=AppInfo(app_id=1459969523, name="Nebula", country="us"),
        sampling=SamplingMetadata(
            provider="itunes",
            method="uniform_random_rank",
            storefront="us",
            population_total=100,
            reachable=100,
            frame_description="newest 100 of 100",
            frame_first_date=datetime(2026, 9, 30, tzinfo=UTC),
            frame_last_date=datetime(2026, 1, 1, tzinfo=UTC),
            requested=100,
            actual=100,
            sample_complete=True,
            sampling_fraction=1.0,
            seed=42,
            ranks=list(range(100)),
            collected_at=datetime(2026, 9, 29, tzinfo=UTC),
            store_histogram=[20, 20, 20, 20, 20],
            store_mean=3.0,
            store_rating_count=100,
        ),
        reviews=reviews,
    )
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "nebula.snapshot.json").write_text(
        snapshot.model_dump_json(indent=2), encoding="utf-8"
    )


def test_collect_fixture_writes_json_and_csv(tmp_path: Path) -> None:
    fixture_dir = tmp_path / "fixtures"
    _write_snapshot(fixture_dir)
    out = tmp_path / "out.json"
    get_settings.cache_clear()
    result = runner.invoke(
        app,
        [
            "collect",
            "--app",
            "1459969523",
            "--country",
            "us",
            "--n",
            "100",
            "--seed",
            "42",
            "--provider",
            "fixture",
            "--out",
            str(out),
        ],
        env={"FIXTURE_DIR": str(fixture_dir)},
    )
    get_settings.cache_clear()
    assert result.exit_code == 0, result.output
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert len(payload["reviews"]) == 100
    assert len({item["source_review_id"] for item in payload["reviews"]}) == 100
    assert payload["sampling"]["seed"] == 42
    assert out.with_suffix(".csv").exists()


def test_collect_invalid_app_exits_nonzero_with_code(tmp_path: Path) -> None:
    get_settings.cache_clear()
    result = runner.invoke(
        app,
        ["collect", "--app", "abc", "--provider", "fixture", "--out", str(tmp_path / "x.json")],
    )
    get_settings.cache_clear()
    assert result.exit_code != 0
    assert "INVALID_INPUT" in result.output


def test_tune_threshold_reads_labelled_pairs_and_prints_selected_value(tmp_path: Path) -> None:
    pairs = tmp_path / "pairs_gold.csv"
    pairs.write_text(
        "distance,same_issue\n0.18,true\n0.22,true\n0.27,true\n0.32,false\n0.36,true\n0.42,false\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["tune-threshold", "--pairs", str(pairs)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["selected_threshold"] == 0.40
    assert payload["minimum_precision"] == 0.8


def test_tune_threshold_rejects_invalid_pair_label(tmp_path: Path) -> None:
    pairs = tmp_path / "pairs_gold.csv"
    pairs.write_text("distance,same_issue\n0.20,maybe\n", encoding="utf-8")
    result = runner.invoke(app, ["tune-threshold", "--pairs", str(pairs)])
    assert result.exit_code != 0
    assert "INVALID_INPUT" in result.output


def test_download_models_eval_adds_pinned_comparator_without_siebert(
    tmp_path: Path, monkeypatch: object
) -> None:
    import appstore_review_analysis.cli as cli
    from appstore_review_analysis.config import Settings

    calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(  # type: ignore[attr-defined]
        cli, "get_settings", lambda: Settings(models_dir=tmp_path)
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        cli, "download_sentiment_model", lambda _path: tmp_path / "shipping"
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        cli, "download_embedding_model", lambda _path: tmp_path / "embedder"
    )

    def fake_eval(_models_dir: Path, *, model_id: str, revision: str, dirname: str) -> Path:
        calls.append((model_id, revision, dirname))
        return tmp_path / dirname

    monkeypatch.setattr(cli, "download_evaluation_model", fake_eval)  # type: ignore[attr-defined]
    result = runner.invoke(app, ["download-models", "--eval"])
    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    assert calls[0][0] == cli.TABULARIS_MODEL_ID
    assert "evaluation_siebert" not in json.loads(result.output)
