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
