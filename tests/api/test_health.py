from fastapi.testclient import TestClient

from appstore_review_analysis.config import Settings
from appstore_review_analysis.main import create_app


def test_healthz_returns_ok_and_request_id() -> None:
    with TestClient(create_app(Settings())) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Request-ID"]


def test_healthz_preserves_supplied_request_id() -> None:
    with TestClient(create_app(Settings())) as client:
        response = client.get("/healthz", headers={"X-Request-ID": "test-request-123"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "test-request-123"


def test_readyz_is_ok_with_injected_sentiment() -> None:
    from appstore_review_analysis.analysis.sentiment import FakeSentiment

    with TestClient(create_app(Settings(), sentiment=FakeSentiment())) as client:
        response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_reports_model_unavailable_when_load_fails(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    def fail_load(_models_dir):  # type: ignore[no-untyped-def]
        raise FileNotFoundError("missing")

    monkeypatch.setattr(
        "appstore_review_analysis.main.TransformerSentiment.load",
        fail_load,
    )
    settings = Settings(
        models_dir=tmp_path / "missing", database_url=f"sqlite:///{tmp_path / 'ready.db'}"
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "MODEL_UNAVAILABLE"


def test_readyz_reports_embedder_unavailable_when_load_fails(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from appstore_review_analysis.analysis.sentiment import FakeSentiment

    def fail_embedder(_models_dir):  # type: ignore[no-untyped-def]
        raise FileNotFoundError("missing")

    monkeypatch.setattr(
        "appstore_review_analysis.main.SentenceTransformerEmbedder.load",
        fail_embedder,
    )
    # Do not inject sentiment here: monkeypatch its loader to keep startup offline
    # while still exercising the real embedder-loading branch.
    monkeypatch.setattr(
        "appstore_review_analysis.main.TransformerSentiment.load",
        lambda _models_dir: FakeSentiment(),
    )
    settings = Settings(
        models_dir=tmp_path / "missing",
        database_url=f"sqlite:///{tmp_path / 'embedder-ready.db'}",
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "MODEL_UNAVAILABLE"
