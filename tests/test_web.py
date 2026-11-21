from fastapi.testclient import TestClient

from ideafinder.web.app import app
from tests.conftest import UNREACHABLE_URL


def test_status_page_explains_unreachable_database(use_settings):
    use_settings(UNREACHABLE_URL)
    response = TestClient(app).get("/status")
    assert response.status_code == 200
    assert "not reachable" in response.text
    assert "Open Postgres.app" in response.text
    assert "Hacker News" in response.text  # config still shown


def test_healthz_reports_problems(use_settings):
    use_settings(UNREACHABLE_URL)
    body = TestClient(app).get("/healthz").json()
    assert body["ok"] is False
    assert body["database"]["reachable"] is False
