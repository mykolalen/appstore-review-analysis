from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from appstore_review_analysis.analysis.sentiment import FakeSentiment
from appstore_review_analysis.analysis.themes import FakeEmbedder
from appstore_review_analysis.config import Settings
from appstore_review_analysis.main import create_app
from appstore_review_analysis.public_mode import PUBLIC_SEED_ANALYSIS_ID

ROOT = Path(__file__).resolve().parents[2]


class _EnglishIdentifier:
    def classify(self, _text: str) -> tuple[str, float]:
        return "en", 0.99


@pytest.fixture(autouse=True)
def _english_language(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "appstore_review_analysis.text._language_identifier",
        lambda: _EnglishIdentifier(),
    )


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "public_mode": True,
        "fixture_dir": ROOT / "data" / "fixtures",
        "database_url": f"sqlite:///{tmp_path / 'public.db'}",
        "public_seed_analysis_path": ROOT / "reports" / "nebula_us_seed42.analysis.json",
        "public_global_post_limit": 20,
        "public_client_post_limit": 20,
        "public_rate_window_s": 600.0,
    }
    values.update(overrides)
    return Settings(**values)


def _body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "app": "1459969523",
        "country": "us",
        "sample_size": 100,
        "seed": 42,
        "analyze": False,
    }
    body.update(overrides)
    return body


def test_public_mode_forces_fixture_default_and_enforces_limits(tmp_path: Path) -> None:
    settings = _settings(tmp_path, public_max_sample_size=100)
    with TestClient(
        create_app(settings, sentiment=FakeSentiment(), embedder=FakeEmbedder())
    ) as client:
        created = client.post("/v1/analyses", json=_body())
        assert created.status_code == 201
        assert created.json()["request"]["provider"] == "fixture"
        assert created.headers["location"].startswith("/v1/analyses/")

        too_large = client.post("/v1/analyses", json=_body(sample_size=101))
        assert too_large.status_code == 422
        assert too_large.json()["error"]["code"] == "INVALID_INPUT"

        rss = client.post("/v1/analyses", json=_body(provider="rss"))
        assert rss.status_code == 422
        assert rss.json()["error"]["code"] == "INVALID_INPUT"


def test_public_mode_global_token_bucket_returns_retry_after(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        public_global_post_limit=2,
        public_client_post_limit=10,
        public_rate_window_s=600.0,
    )
    with TestClient(
        create_app(settings, sentiment=FakeSentiment(), embedder=FakeEmbedder())
    ) as client:
        for suffix in ("1", "2"):
            response = client.post(
                "/v1/analyses",
                json=_body(),
                headers={"X-Forwarded-For": f"198.51.100.{suffix}"},
            )
            assert response.status_code == 201
        limited = client.post(
            "/v1/analyses",
            json=_body(),
            headers={"X-Forwarded-For": "198.51.100.3"},
        )
        assert limited.status_code == 429
        assert limited.json()["error"]["code"] == "RATE_LIMITED"
        assert set(limited.json()["error"]) == {"code", "message", "request_id", "details"}
        assert int(limited.headers["retry-after"]) > 0


def test_public_mode_uses_rightmost_forwarded_for_for_client_limit(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        public_global_post_limit=10,
        public_client_post_limit=1,
        public_rate_window_s=600.0,
    )
    with TestClient(
        create_app(settings, sentiment=FakeSentiment(), embedder=FakeEmbedder())
    ) as client:
        first = client.post(
            "/v1/analyses",
            json=_body(),
            headers={"X-Forwarded-For": "203.0.113.10, 198.51.100.20"},
        )
        assert first.status_code == 201

        forged_left = client.post(
            "/v1/analyses",
            json=_body(),
            headers={"X-Forwarded-For": "203.0.113.99, 198.51.100.20"},
        )
        assert forged_left.status_code == 429
        assert forged_left.json()["error"]["code"] == "RATE_LIMITED"

        different_rightmost = client.post(
            "/v1/analyses",
            json=_body(),
            headers={"X-Forwarded-For": "203.0.113.10, 198.51.100.21"},
        )
        assert different_rightmost.status_code == 201


def test_public_seed_is_recreated_under_fixed_id_after_empty_db_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "public.db"
    settings = _settings(tmp_path, database_url=f"sqlite:///{database_path}")

    with TestClient(
        create_app(settings, sentiment=FakeSentiment(), embedder=FakeEmbedder())
    ) as client:
        first = client.get(f"/v1/analyses/{PUBLIC_SEED_ANALYSIS_ID}")
        assert first.status_code == 200
        assert first.json()["analysis_id"] == PUBLIC_SEED_ANALYSIS_ID

    database_path.unlink()

    with TestClient(
        create_app(settings, sentiment=FakeSentiment(), embedder=FakeEmbedder())
    ) as client:
        second = client.get(f"/v1/analyses/{PUBLIC_SEED_ANALYSIS_ID}")
        assert second.status_code == 200
        assert second.json()["analysis_id"] == PUBLIC_SEED_ANALYSIS_ID


def test_public_seed_serves_its_reviews_for_download(tmp_path: Path) -> None:
    # Regression: the seed used to be stored without rows, so its download was empty.
    with TestClient(
        create_app(_settings(tmp_path), sentiment=FakeSentiment(), embedder=FakeEmbedder())
    ) as client:
        analysis = client.get(f"/v1/analyses/{PUBLIC_SEED_ANALYSIS_ID}").json()
        reviews = client.get(f"/v1/analyses/{PUBLIC_SEED_ANALYSIS_ID}/reviews?format=json")
        csv_response = client.get(f"/v1/analyses/{PUBLIC_SEED_ANALYSIS_ID}/reviews?format=csv")

    assert reviews.status_code == 200
    assert len(reviews.json()) == analysis["sampling"]["actual"] == 100
    assert len(list(csv.DictReader(io.StringIO(csv_response.text)))) == 100


def test_repeated_forwarded_for_headers_use_the_right_most_hop(tmp_path: Path) -> None:
    from starlette.requests import Request

    from appstore_review_analysis.public_mode import public_client_key

    scope = {
        "type": "http",
        "headers": [
            (b"x-forwarded-for", b"203.0.113.9"),
            (b"x-forwarded-for", b"198.51.100.7"),
        ],
        "client": ("10.0.0.1", 1234),
    }
    assert public_client_key(Request(scope)) == "198.51.100.7"
