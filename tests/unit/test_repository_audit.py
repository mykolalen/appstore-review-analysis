"""Cross-file repository contracts checked during the final audit."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from appstore_review_analysis import docker_entrypoint

ROOT = Path(__file__).resolve().parents[2]
SETUP_UV_SHA = "c18668ad3cf93ea998bef934396af7bb5c839dc7"


def test_ci_is_pinned_cross_platform_and_uses_only_fast_offline_gate() -> None:
    text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert text.count(f"astral-sh/setup-uv@{SETUP_UV_SHA}") == 2
    assert 'version: "0.12.19"' in text
    assert "os: [ubuntu-latest, windows-latest]" in text
    assert 'uv run --locked pytest -m "not slow and not live"' in text
    assert "pytest -m live" not in text
    assert '"provider":"itunes"' not in text
    assert "API_KEY" not in text


def test_docker_entrypoint_honours_port(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_run(app: str, **kwargs: object) -> None:
        seen["app"] = app
        seen.update(kwargs)

    monkeypatch.setenv("PORT", "9123")
    monkeypatch.setattr(docker_entrypoint.uvicorn, "run", fake_run)
    docker_entrypoint.main()

    assert seen == {
        "app": "appstore_review_analysis.main:app",
        "host": "0.0.0.0",
        "port": 9123,
        "workers": 1,
    }


def test_evaluation_csv_writers_force_lf() -> None:
    paths = sorted((ROOT / "evaluation").glob("*.py")) + [
        ROOT / "src/appstore_review_analysis/evaluation.py"
    ]
    calls: list[tuple[Path, ast.Call]] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id == "csv"
                and node.func.attr == "DictWriter"
            ):
                calls.append((path, node))

    assert calls, "expected evaluation CSV writers to be present"
    failures: list[str] = []
    for path, call in calls:
        keyword = next((kw for kw in call.keywords if kw.arg == "lineterminator"), None)
        if (
            keyword is None
            or not isinstance(keyword.value, ast.Constant)
            or keyword.value.value != "\n"
        ):
            failures.append(f"{path.relative_to(ROOT)}:{call.lineno}")
    assert not failures, "evaluation CSV writers without LF terminator: " + ", ".join(failures)


def test_committed_evaluation_csv_artifacts_use_lf_only() -> None:
    for name in [
        "gold_items.csv",
        "labels_final.csv",
        "labels_relabel.csv",
    ]:
        payload = (ROOT / "evaluation" / name).read_bytes()
        assert b"\r\n" not in payload, f"{name} contains CRLF"
        assert b"\n" in payload, f"{name} contains no line endings"


def test_readme_evaluation_claims_match_committed_results() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    results = json.loads((ROOT / "evaluation/results.json").read_text(encoding="utf-8"))
    benchmark = results["benchmark"]
    models = {item["name"]: item for item in benchmark["models"]}
    shipping = models["shipping_cardiffnlp"]["metrics"]
    tabularis = models["tabularisai_robust_sentiment"]["metrics"]
    paired = benchmark["paired_tabularis_minus_shipping_negative_f1_ci95"]

    assert f"{benchmark['evaluable_n']} reviews are evaluable" in readme
    assert f"{benchmark['mixed_n']} are mixed" in readme
    assert f"{shipping['accuracy']:.3f} accuracy" in readme
    assert f"{shipping['macro_f1']:.3f} macro-F1" in readme
    assert f"{shipping['negative_f1']:.3f} negative-class F1" in readme
    tabularis_triplet = (
        f"{tabularis['accuracy']:.3f} / {tabularis['macro_f1']:.3f} / "
        f"{tabularis['negative_f1']:.3f}"
    )
    assert tabularis_triplet in readme
    assert f"[{paired['low']:.3f}, {paired['high']:.3f}]" in readme
    assert "Sentiment accuracy has not yet been measured" not in readme
    assert "completed hand-labelled evaluation" not in readme
    assert "later-session 30-item repeat-labelling check" not in readme


def test_requirement_table_points_to_existing_implementation() -> None:
    required_paths = [
        "src/appstore_review_analysis/collection/itunes.py",
        "src/appstore_review_analysis/collection/rss.py",
        "src/appstore_review_analysis/collection/sampling.py",
        "src/appstore_review_analysis/public_mode.py",
        "src/appstore_review_analysis/errors.py",
        "src/appstore_review_analysis/domain.py",
        "src/appstore_review_analysis/text.py",
        "src/appstore_review_analysis/analysis/metrics.py",
        "src/appstore_review_analysis/analysis/sentiment.py",
        "src/appstore_review_analysis/analysis/keywords.py",
        "src/appstore_review_analysis/analysis/themes.py",
        "src/appstore_review_analysis/analysis/evidence.py",
        "reports/nebula_us_seed42.md",
        "docs/architecture.md",
        "docs/decisions.md",
    ]
    missing = [path for path in required_paths if not (ROOT / path).is_file()]
    assert not missing, f"README requirement table points to missing paths: {missing}"
