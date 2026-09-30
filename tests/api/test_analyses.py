from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from appstore_review_analysis import cli
from appstore_review_analysis.analysis.sentiment import FakeSentiment
from appstore_review_analysis.analysis.themes import FakeEmbedder
from appstore_review_analysis.config import Settings
from appstore_review_analysis.domain import AppInfo, CollectionResult, Review, SamplingMetadata
from appstore_review_analysis.main import create_app


class _EnglishIdentifier:
    def classify(self, _text: str) -> tuple[str, float]:
        return "en", 0.99


def _review(index: int, rating: int, *, long: bool = False) -> Review:
    body = "great useful app" if rating >= 4 else "bad charged refund problem"
    if long:
        body = "great " * 3001
    year = 2026 if index < 35 else 2025 if index < 70 else 2024
    return Review(
        source_review_id=f"review-{index}",
        rank=index,
        country="us",
        title=f"Review {index}",
        body=body,
        rating=rating,
        created_at=datetime(year, 1, 1, tzinfo=UTC),
        is_edited=False,
        vote_count=index,
        vote_sum=index,
        has_developer_response=False,
        developer_response_id=None,
        developer_response_date=None,
        provider="fixture",
    )


def _snapshot(
    path: Path,
    *,
    requested: int,
    seed: int,
    ratings: list[int],
    reachable: int = 1000,
) -> Path:
    reviews = [
        _review(index, rating, long=index == 0 and len(ratings) > 20)
        for index, rating in enumerate(ratings)
    ]
    sampling = SamplingMetadata(
        provider="itunes",
        method="uniform_random_rank",
        storefront="us",
        population_total=reachable,
        reachable=reachable,
        frame_description=f"newest {reachable} of {reachable}",
        frame_first_date=datetime(2026, 9, 29, tzinfo=UTC) if reachable else None,
        frame_last_date=datetime(2019, 1, 1, tzinfo=UTC) if reachable else None,
        requested=requested,
        actual=len(reviews),
        sample_complete=len(reviews) == requested,
        sampling_fraction=(len(reviews) / reachable if reachable else None),
        seed=seed,
        ranks=list(range(len(reviews))),
        collected_at=datetime(2026, 9, 29, tzinfo=UTC),
        store_histogram=[10, 10, 10, 20, 50],
        store_mean=3.8,
        store_rating_count=100,
    )
    result = CollectionResult(
        app=AppInfo(
            app_id=1459969523,
            name="Nebula: Spiritual Guidance",
            country="us",
            average_user_rating=4.57,
            user_rating_count=170_000,
            current_version_release_date=datetime(2026, 9, 1, tzinfo=UTC),
        ),
        sampling=sampling,
        reviews=reviews,
        warnings=[] if len(reviews) == requested else ["partial sample"],
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def _settings(tmp_path: Path) -> Settings:
    fixture_dir = tmp_path / "fixtures"
    ratings = [1] * 14 + [2] * 4 + [3] * 6 + [4] * 12 + [5] * 64
    _snapshot(fixture_dir / "main.snapshot.json", requested=100, seed=42, ratings=ratings)
    _snapshot(fixture_dir / "single.snapshot.json", requested=1, seed=8, ratings=[5])
    _snapshot(fixture_dir / "constant.snapshot.json", requested=10, seed=9, ratings=[5] * 10)
    _snapshot(fixture_dir / "zero.snapshot.json", requested=100, seed=7, ratings=[], reachable=0)
    return Settings(
        default_provider="fixture",
        fixture_dir=fixture_dir,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        request_deadline_s=90,
    )


def _patch_language(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        "appstore_review_analysis.text._language_identifier",
        lambda: _EnglishIdentifier(),
    )


def test_post_get_metrics_and_csv_round_trip(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _patch_language(monkeypatch)
    settings = _settings(tmp_path)
    with TestClient(create_app(settings, sentiment=FakeSentiment())) as client:
        create = client.post(
            "/v1/analyses",
            json={
                "app": "1459969523",
                "country": "us",
                "sample_size": 100,
                "seed": 42,
                "provider": "fixture",
            },
        )
        assert create.status_code == 201
        payload = create.json()
        analysis_id = payload["analysis_id"]
        assert create.headers["location"] == f"/v1/analyses/{analysis_id}"
        assert payload["metrics"]["mean"] == 4.08
        assert payload["metrics"]["store_benchmark"]["mean"] == 3.8
        assert payload["metrics"]["periods"]
        assert payload["preprocessing"]["n_all"] == 100

        fetched = client.get(f"/v1/analyses/{analysis_id}")
        assert fetched.status_code == 200
        assert fetched.json()["metrics"] == payload["metrics"]

        metrics = client.get(f"/v1/analyses/{analysis_id}/metrics")
        assert metrics.status_code == 200
        assert metrics.json()["metrics"] == payload["metrics"]

        csv_response = client.get(f"/v1/analyses/{analysis_id}/reviews?format=csv")
        assert csv_response.status_code == 200
        assert f"reviews-{analysis_id}.csv" in csv_response.headers["content-disposition"]
        assert "truncated" in csv_response.text
        assert "True" in csv_response.text

        json_response = client.get(f"/v1/analyses/{analysis_id}/reviews?format=json")
        assert json_response.status_code == 200
        assert len(json_response.json()) == 100
        assert json_response.json()[0]["truncated"] is True


def test_same_seed_produces_identical_metrics(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _patch_language(monkeypatch)
    settings = _settings(tmp_path)
    with TestClient(create_app(settings, sentiment=FakeSentiment())) as client:
        body = {
            "app": 1459969523,
            "country": "us",
            "sample_size": 100,
            "seed": 42,
            "provider": "fixture",
        }
        first = client.post("/v1/analyses", json=body)
        second = client.post("/v1/analyses", json=body)
        assert first.status_code == second.status_code == 201
        assert first.json()["metrics"] == second.json()["metrics"]


def test_degenerate_fixture_inputs_return_valid_json(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _patch_language(monkeypatch)
    settings = _settings(tmp_path)
    with TestClient(create_app(settings, sentiment=FakeSentiment())) as client:
        for sample_size, seed, reason in [
            (1, 8, "degenerate"),
            (10, 9, "degenerate"),
            (100, 7, "no_reviews"),
        ]:
            response = client.post(
                "/v1/analyses",
                json={
                    "app": 1459969523,
                    "country": "us",
                    "sample_size": sample_size,
                    "seed": seed,
                    "provider": "fixture",
                },
            )
            assert response.status_code == 201
            assert response.json()["metrics"]["mean_ci95"]["reason"] == reason


def test_collect_only_does_not_need_sentiment_model(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _patch_language(monkeypatch)
    settings = _settings(tmp_path).model_copy(update={"models_dir": tmp_path / "missing-models"})
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/v1/analyses",
            json={
                "app": 1459969523,
                "country": "us",
                "sample_size": 100,
                "seed": 42,
                "provider": "fixture",
                "analyze": False,
            },
        )
        assert response.status_code == 201
        assert response.json()["sentiment"]["status"] == "not_requested"

        unavailable = client.post(
            "/v1/analyses",
            json={
                "app": 1459969523,
                "country": "us",
                "sample_size": 100,
                "seed": 42,
                "provider": "fixture",
                "analyze": True,
            },
        )
        assert unavailable.status_code == 503
        assert unavailable.json()["error"]["code"] == "MODEL_UNAVAILABLE"


def test_unknown_analysis_and_bad_delimiter_use_error_schema(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _patch_language(monkeypatch)
    settings = _settings(tmp_path)
    with TestClient(create_app(settings, sentiment=FakeSentiment())) as client:
        missing = client.get("/v1/analyses/missing")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "ANALYSIS_NOT_FOUND"

        created = client.post(
            "/v1/analyses",
            json={
                "app": 1459969523,
                "sample_size": 100,
                "seed": 42,
                "provider": "fixture",
            },
        )
        analysis_id = created.json()["analysis_id"]
        bad = client.get(f"/v1/analyses/{analysis_id}/reviews?format=csv&delimiter=%7C")
        assert bad.status_code == 422
        assert bad.json()["error"]["code"] == "INVALID_INPUT"


def test_cli_and_post_have_identical_metrics(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _patch_language(monkeypatch)
    settings = _settings(tmp_path)
    snapshot = settings.fixture_dir / "main.snapshot.json"

    with TestClient(create_app(settings, sentiment=FakeSentiment())) as client:
        api_response = client.post(
            "/v1/analyses",
            json={
                "app": 1459969523,
                "country": "us",
                "sample_size": 100,
                "seed": 42,
                "provider": "fixture",
            },
        )
    assert api_response.status_code == 201

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "_runtime_sentiment", lambda _settings: FakeSentiment())
    monkeypatch.setattr(cli, "_runtime_embedder", lambda _settings: FakeEmbedder())
    out = tmp_path / "analysis.json"
    result = CliRunner().invoke(
        cli.app,
        [
            "analyze",
            "--provider",
            "fixture",
            "--snapshot",
            str(snapshot),
            "--out",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    cli_payload = json.loads(out.read_text(encoding="utf-8"))
    assert cli_payload["metrics"] == api_response.json()["metrics"]


class _BrokenSentiment:
    def predict(self, _texts: list[str]):  # type: ignore[no-untyped-def]
        raise RuntimeError("boom")


def test_model_failure_returns_503(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _patch_language(monkeypatch)
    settings = _settings(tmp_path)
    with TestClient(create_app(settings, sentiment=_BrokenSentiment())) as client:
        response = client.post(
            "/v1/analyses",
            json={
                "app": 1459969523,
                "country": "us",
                "sample_size": 100,
                "seed": 42,
                "provider": "fixture",
            },
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "MODEL_UNAVAILABLE"


def test_expired_deadline_before_sentiment_returns_504(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _patch_language(monkeypatch)
    settings = _settings(tmp_path).model_copy(update={"request_deadline_s": 1e-12})
    with TestClient(create_app(settings, sentiment=FakeSentiment())) as client:
        response = client.post(
            "/v1/analyses",
            json={
                "app": 1459969523,
                "country": "us",
                "sample_size": 100,
                "seed": 42,
                "provider": "fixture",
            },
        )

    assert response.status_code == 504
    assert response.json()["error"]["code"] == "ANALYSIS_DEADLINE_EXCEEDED"


def test_confident_non_english_review_is_excluded_from_sentiment(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    settings = _settings(tmp_path)
    snapshot = settings.fixture_dir / "main.snapshot.json"
    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    payload["reviews"][0]["title"] = "Reseña"
    payload["reviews"][0]["body"] = "hola esta aplicación tiene un problema terrible y no funciona"
    snapshot.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    class _MixedIdentifier:
        def classify(self, text: str) -> tuple[str, float]:
            return ("es", 0.99) if "hola" in text.lower() else ("en", 0.99)

    monkeypatch.setattr(
        "appstore_review_analysis.text._language_identifier",
        lambda: _MixedIdentifier(),
    )
    with TestClient(create_app(settings, sentiment=FakeSentiment())) as client:
        response = client.post(
            "/v1/analyses",
            json={
                "app": 1459969523,
                "country": "us",
                "sample_size": 100,
                "seed": 42,
                "provider": "fixture",
            },
        )

    assert response.status_code == 201
    body = response.json()
    assert body["preprocessing"]["n_analysable"] == 99
    assert body["preprocessing"]["language_counts"]["es"] == 1
    assert body["sentiment"]["n_analysable"] == 99
    assert sum(item["count"] for item in body["sentiment"]["distribution"].values()) == 99


def test_missing_embedder_returns_503(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _patch_language(monkeypatch)
    settings = _settings(tmp_path)
    monkeypatch.setattr(
        "appstore_review_analysis.main.TransformerSentiment.load",
        lambda _models_dir: FakeSentiment(),
    )

    def fail_embedder(_models_dir):  # type: ignore[no-untyped-def]
        raise FileNotFoundError("missing")

    monkeypatch.setattr(
        "appstore_review_analysis.main.SentenceTransformerEmbedder.load",
        fail_embedder,
    )
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/v1/analyses",
            json={
                "app": 1459969523,
                "country": "us",
                "sample_size": 100,
                "seed": 42,
                "provider": "fixture",
            },
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "MODEL_UNAVAILABLE"


def test_slice5_outputs_keywords_themes_and_areas(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _patch_language(monkeypatch)
    settings = _settings(tmp_path)
    with TestClient(create_app(settings, sentiment=FakeSentiment())) as client:
        response = client.post(
            "/v1/analyses",
            json={
                "app": 1459969523,
                "country": "us",
                "sample_size": 100,
                "seed": 42,
                "provider": "fixture",
            },
        )

    assert response.status_code == 201
    body = response.json()
    assert body["keywords"]["status"] == "ok"
    assert body["keywords"]["common"]
    assert body["themes"]["status"] in {"ok", "no_supported_clusters"}
    assert body["preprocessing"]["n_units"] > 0
    assert body["insights"]["areas_of_improvement"]
    for area in body["insights"]["areas_of_improvement"]:
        assert area["evidence_review_ids"]
