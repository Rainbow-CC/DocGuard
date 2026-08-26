from fastapi.testclient import TestClient

from docguard.api.app import app


def test_dashboard_and_assets_are_served() -> None:
    client = TestClient(app)

    page = client.get("/")

    assert page.status_code == 200
    assert "DOCGUARD" in page.text
    assert 'id="details-button"' in page.text
    assert 'id="continue-button"' in page.text
    assert 'id="findings-summary"' in page.text
    assert 'id="findings-pagination"' in page.text
    assert 'id="task-filename-filter"' in page.text
    assert 'id="review-type"' in page.text
    assert 'dashboard.js?v=task-expand-v2' in page.text
    assert client.get("/static/dashboard.css").status_code == 200
    assert client.get("/static/dashboard.js").status_code == 200


def test_task_list_endpoint_returns_a_list() -> None:
    response = TestClient(app).get("/api/v1/tasks")

    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_review_types_endpoint_returns_enabled_platform_types() -> None:
    response = TestClient(app).get("/api/v1/review-types")

    assert response.status_code == 200
    assert response.json()[0]["review_type_id"] == "technical-architecture"
