from fastapi.testclient import TestClient

from contract_app.api import app


def test_health_live() -> None:
    response = TestClient(app).get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert app.openapi()["openapi"] == "3.1.0"
