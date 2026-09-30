"""Documentation/container structure checks."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_readme_has_required_sections_and_smoke_markers() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    required = [
        "## Requirements mapped to the repository",
        "## Quickstart",
        "## Approach",
        "## Demo report",
        "## Measured numbers",
        "## Design decisions",
        "## Privacy, licences and attribution",
    ]
    for heading in required:
        assert heading in text
    assert "<!-- readme-smoke:start -->" in text
    assert "<!-- readme-smoke:end -->" in text
    assert "reports/nebula_us_seed42.md" in text


def test_mermaid_blocks_are_balanced_and_non_empty() -> None:
    for relative in ["README.md", "docs/architecture.md", "docs/decisions.md"]:
        text = (ROOT / relative).read_text(encoding="utf-8")
        starts = [match.start() for match in re.finditer(r"```mermaid\n", text)]
        assert text.count("```mermaid") == len(starts)
        for start in starts:
            body_start = start + len("```mermaid\n")
            body_end = text.find("```", body_start)
            assert body_end > body_start
            body = text[body_start:body_end].strip()
            assert body.startswith(("flowchart", "graph", "sequenceDiagram", "classDiagram"))


def test_every_adr_has_required_sections() -> None:
    text = (ROOT / "docs/decisions.md").read_text(encoding="utf-8")
    adrs = re.split(r"(?=^## ADR-\d{3})", text, flags=re.MULTILINE)[1:]
    assert len(adrs) == 11
    for adr in adrs:
        assert "### Context" in adr
        assert "### Decision" in adr
        assert "### Rejected alternatives" in adr
        assert "### Evidence" in adr
        evidence = adr.split("### Evidence", maxsplit=1)[1]
        assert "](" in evidence


def test_container_files_keep_fixture_and_reports() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert "FROM python:3.13-slim" in dockerfile
    assert "ghcr.io/astral-sh/uv:0.12.19" in dockerfile
    assert "COPY data/fixtures ./data/fixtures" in dockerfile
    assert "COPY reports ./reports" in dockerfile
    assert "ENV MODELS_DIR=/opt/models" in dockerfile
    assert "RUN reviews download-models" in dockerfile
    assert dockerfile.index("RUN reviews download-models") < dockerfile.index("HF_HUB_OFFLINE=1")
    assert "USER app" in dockerfile
    assert 'CMD ["python", "-m", "appstore_review_analysis.docker_entrypoint"]' in dockerfile

    assert "/data/raw" in dockerignore
    assert "/models" in dockerignore
    assert "/data/fixtures" not in dockerignore
    assert "/reports" not in dockerignore

    assert "app_data:/app/var" in compose
    assert "API_KEY" not in compose
    assert "PUBLIC_MODE" in compose
    assert (ROOT / "docs/deploy_cloud_run.md").is_file()


FORBIDDEN_REVIEWER_WORDS = re.compile(
    r"\b(?:slice|scoped build|handoff|roadmap)\b",
    re.IGNORECASE,
)


def test_reviewer_visible_files_and_cli_help_do_not_leak_internal_process_wording() -> None:
    from typer.testing import CliRunner

    from appstore_review_analysis.cli import app

    paths = [ROOT / "README.md"]
    paths.extend(sorted((ROOT / "docs").rglob("*.md")))
    paths.extend(sorted((ROOT / "reports").glob("*.md")))
    paths.extend(sorted((ROOT / "src").rglob("*.py")))
    hits: list[str] = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for match in FORBIDDEN_REVIEWER_WORDS.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            hits.append(f"{path.relative_to(ROOT)}:{line}:{match.group(0)}")

    runner = CliRunner()
    for args in [
        ["--help"],
        ["collect", "--help"],
        ["analyze", "--help"],
        ["audit-categories", "--help"],
        ["report", "--help"],
        ["tune-threshold", "--help"],
        ["serve", "--help"],
        ["walk", "--help"],
        ["download-models", "--help"],
    ]:
        result = runner.invoke(app, args)
        assert result.exit_code == 0, result.output
        for match in FORBIDDEN_REVIEWER_WORDS.finditer(result.output):
            hits.append(f"CLI {' '.join(args)}:{match.group(0)}")

    message = "internal process wording leaked to reviewer-visible output:\n" + "\n".join(hits)
    assert not hits, message


# Assistant/tool names are assembled from fragments so this guard does not itself contain them.
_ASSISTANT_NAMES = re.compile(
    "|".join(["chat" + "gpt", "clau" + "de", r"gpt-\d", "open" + "ai", "anthro" + "pic"]),
    re.IGNORECASE,
)
_TEXT_SUFFIXES = {".py", ".md", ".yml", ".yaml", ".toml", ".example", ".json", ".txt"}
_SKIP_DIRS = {".git", ".venv", "models", "data", "__pycache__", ".mypy_cache", ".ruff_cache"}


def _authored_text_files() -> list[Path]:
    files: list[Path] = []
    for path in sorted(ROOT.rglob("*")):
        relative = path.relative_to(ROOT)
        if not path.is_file() or any(part in _SKIP_DIRS for part in relative.parts):
            continue
        # Fixtures, gold data and the lock file hold verbatim third-party review text or hashes.
        if relative.parts[0] == "tests" and "fixtures" in relative.parts:
            continue
        if relative.name == "uv.lock" or relative.suffix == ".csv":
            continue
        if path.suffix in _TEXT_SUFFIXES or path.name in {"Dockerfile", "compose.yaml", "NOTICE"}:
            files.append(path)
    return files


def test_repository_text_does_not_name_ai_assistants() -> None:
    hits: list[str] = []
    for path in _authored_text_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in _ASSISTANT_NAMES.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            hits.append(f"{path.relative_to(ROOT)}:{line}")
    assert not hits, "assistant names found in authored files: " + ", ".join(hits)


def test_no_pre_labelling_artefacts_remain() -> None:
    evaluation = ROOT / "evaluation"
    leftovers = [
        name
        for name in (
            "prelabels.jsonl",
            "prelabel_batch.json",
            "prelabel_response.json",
            "labels_blind.csv",
            "PRELABEL_PROMPT.md",
        )
        if (evaluation / name).exists()
    ]
    assert not leftovers, f"pre-labelling artefacts still present: {leftovers}"
