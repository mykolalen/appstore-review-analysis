"""Slow end-to-end reproduction check against the committed real snapshot."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from appstore_review_analysis.analysis.pipeline import analyse_collection
from appstore_review_analysis.analysis.sentiment import TransformerSentiment
from appstore_review_analysis.analysis.themes import (
    DEFAULT_DISTANCE_THRESHOLD,
    SentenceTransformerEmbedder,
)
from appstore_review_analysis.domain import CollectionResult
from appstore_review_analysis.text import preprocess_review


@pytest.mark.slow
def test_fixture_reproduction_matches_committed_analysis_to_float_tolerance() -> None:
    root = Path(__file__).resolve().parents[2]
    snapshot_path = root / "data/fixtures/nebula_us_seed42.snapshot.json"
    committed_path = root / "reports/nebula_us_seed42.analysis.json"
    if not (snapshot_path.exists() and committed_path.exists()):
        pytest.skip("Real demo artifacts must be generated locally first.")

    collection = CollectionResult.model_validate(
        json.loads(snapshot_path.read_text(encoding="utf-8"))
    )
    sentiment = TransformerSentiment.load(root / "models")
    embedder = SentenceTransformerEmbedder.load(root / "models")
    reproduced, _rows = analyse_collection(
        collection,
        request_payload={
            "app": str(collection.app.app_id),
            "country": collection.app.country,
            "sample_size": collection.sampling.requested,
            "seed": collection.sampling.seed,
            "provider": "fixture",
            "analyze": True,
            "distance_threshold": DEFAULT_DISTANCE_THRESHOLD,
        },
        sentiment=sentiment,
        embedder=embedder,
        analyze=True,
        request_deadline_s=300.0,
        theme_distance_threshold=DEFAULT_DISTANCE_THRESHOLD,
    )
    committed = json.loads(committed_path.read_text(encoding="utf-8"))
    actual = reproduced.model_dump(mode="json")
    _drop_volatile(actual)
    _drop_volatile(committed)
    _assert_equivalent(actual, committed)
    _assert_excerpts_come_from_reviews(committed, collection)


def _drop_volatile(payload: dict[str, Any]) -> None:
    payload.pop("analysis_id", None)
    payload.pop("created_at", None)
    provenance = payload.get("provenance")
    if isinstance(provenance, dict):
        provenance.pop("timings_ms", None)


def _assert_equivalent(left: Any, right: Any, path: str = "root") -> None:
    if isinstance(left, float) and isinstance(right, (float, int)):
        assert left == pytest.approx(float(right), abs=1e-6), path
        return
    if isinstance(right, float) and isinstance(left, int):
        assert float(left) == pytest.approx(right, abs=1e-6), path
        return
    assert type(left) is type(right), f"{path}: {type(left)} != {type(right)}"
    if isinstance(left, dict):
        assert set(left) == set(right), f"{path}: key mismatch"
        for key in left:
            _assert_equivalent(left[key], right[key], f"{path}.{key}")
    elif isinstance(left, list):
        assert len(left) == len(right), f"{path}: length mismatch"
        for index, (a, b) in enumerate(zip(left, right, strict=True)):
            _assert_equivalent(a, b, f"{path}[{index}]")
    else:
        assert left == right, path


def _assert_excerpts_come_from_reviews(
    analysis: dict[str, Any], collection: CollectionResult
) -> None:
    by_id = {
        review.source_review_id: preprocess_review(review).analysis_text
        for review in collection.reviews
    }
    themes = analysis.get("themes", {})
    if not isinstance(themes, dict):
        return
    items = themes.get("items", [])
    if not isinstance(items, list):
        return
    for theme in items:
        if not isinstance(theme, dict):
            continue
        representatives = theme.get("representative_units", [])
        if not isinstance(representatives, list):
            continue
        for unit in representatives:
            if not isinstance(unit, dict):
                continue
            review_id = str(unit.get("review_id", ""))
            excerpt = str(unit.get("excerpt", ""))
            assert review_id in by_id
            assert excerpt in by_id[review_id]
