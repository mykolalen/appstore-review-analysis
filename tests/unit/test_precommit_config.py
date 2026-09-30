"""Contracts for the repository pre-commit and secret-scanning configuration."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / ".pre-commit-config.yaml"
BASELINE_PATH = ROOT / ".secrets.baseline"


def _config() -> dict[str, Any]:
    loaded = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _locked_version(package_name: str) -> str:
    with (ROOT / "uv.lock").open("rb") as handle:
        lock = tomllib.load(handle)
    matches = [
        package["version"] for package in lock["package"] if package.get("name") == package_name
    ]
    assert len(matches) == 1, f"expected exactly one locked {package_name} package"
    return str(matches[0])


def test_remote_hooks_use_full_pinned_tags_and_ruff_matches_lock() -> None:
    config = _config()
    remote_repos = [repo for repo in config["repos"] if repo["repo"] != "local"]

    assert remote_repos
    for repo in remote_repos:
        rev = repo.get("rev")
        assert isinstance(rev, str)
        assert re.fullmatch(r"v\d+\.\d+\.\d+", rev), (repo["repo"], rev)

    ruff_repo = next(
        repo
        for repo in remote_repos
        if repo["repo"] == "https://github.com/astral-sh/ruff-pre-commit"
    )
    assert ruff_repo["rev"] == f"v{_locked_version('ruff')}"


def test_detect_secrets_uses_empty_portable_baseline_and_expected_excludes() -> None:
    config = _config()
    detect_repo = next(
        repo for repo in config["repos"] if repo["repo"] == "https://github.com/Yelp/detect-secrets"
    )
    hook = next(hook for hook in detect_repo["hooks"] if hook["id"] == "detect-secrets")

    assert hook["args"] == ["--baseline", ".secrets.baseline"]
    exclude = re.compile(hook["exclude"])
    for path in [
        "uv.lock",
        "data/fixtures/x.json",
        "reports/a.md",
        "evaluation/gold_items.csv",
        "tests/fixtures/itunes/a.json",
    ]:
        assert exclude.search(path), path
    assert not exclude.search("src/appstore_review_analysis/config.py")

    raw = BASELINE_PATH.read_bytes()
    assert b"\r" not in raw
    text = raw.decode("utf-8")
    baseline = json.loads(text)
    assert baseline["results"] == {}
    assert "\\" not in text
    assert not re.search(r"(?i)(?:[a-z]:[/\\]|/(?:home|users|mnt|tmp)/)", text)


def test_expected_stages_and_local_hooks_are_configured() -> None:
    config = _config()
    assert config["default_install_hook_types"] == ["pre-commit", "pre-push"]

    local_repo = next(repo for repo in config["repos"] if repo["repo"] == "local")
    hooks = {hook["id"]: hook for hook in local_repo["hooks"]}

    assert hooks["uv-lock-check"]["entry"] == "uv lock --check"
    assert hooks["uv-lock-check"]["stages"] == ["pre-commit"]
    assert hooks["mypy"]["entry"] == "uv run --locked mypy src"
    assert hooks["mypy"]["stages"] == ["pre-push"]
    assert hooks["pytest-fast"]["entry"] == 'uv run --locked pytest -m "not slow and not live" -q'
    assert hooks["pytest-fast"]["stages"] == ["pre-push"]
    assert hooks["gitleaks"]["entry"] == "gitleaks git --staged --pre-commit --redact -v"
    assert hooks["gitleaks"]["stages"] == ["manual"]
