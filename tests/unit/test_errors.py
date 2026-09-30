from fastapi.testclient import TestClient

from appstore_review_analysis.config import Settings
from appstore_review_analysis.main import create_app


def test_unknown_route_uses_error_schema() -> None:
    with TestClient(create_app(Settings())) as client:
        response = client.get("/does-not-exist")

    assert response.status_code == 404
    payload = response.json()
    assert payload == {
        "error": {
            "code": "NOT_FOUND",
            "message": "Resource not found.",
            "request_id": response.headers["X-Request-ID"],
            "details": {},
        }
    }


def test_unexpected_exception_still_uses_error_schema() -> None:
    application = create_app(Settings())

    @application.get("/boom")
    def boom() -> None:
        raise RuntimeError("unexpected")

    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.get("/boom")

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "unexpected" not in response.text


def test_method_not_allowed_keeps_the_allow_header() -> None:
    with TestClient(create_app(Settings())) as client:
        response = client.delete("/healthz")

    assert response.status_code == 405
    assert "GET" in response.headers["allow"]
    assert response.json()["error"]["code"] == "HTTP_ERROR"
