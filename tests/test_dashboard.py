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
    assert 'id="task-project-filter"' in page.text
    assert 'id="project"' in page.text
    assert 'id="create-project-button"' in page.text
    assert 'id="project-dialog"' in page.text
    assert 'id="review-type"' in page.text
    assert 'class="brand-logo"' in page.text
    assert 'logo.jpg?v=projects-filter-v1' in page.text
    assert 'id="user-management-button"' in page.text
    assert 'id="approval-rules-menu"' in page.text
    assert 'id="home-page"' in page.text
    assert 'id="audit-page"' in page.text
    assert 'id="tasks-page"' in page.text
    assert 'id="approval-rules-page"' in page.text
    assert 'id="approval-rules-outline"' in page.text
    assert 'id="page-tabs"' in page.text
    assert 'dashboard.js?v=projects-filter-v1' in page.text
    assert client.get("/static/dashboard.css").status_code == 200
    assert client.get("/static/dashboard.js").status_code == 200
    assert client.get("/static/logo.jpg").status_code == 200


def test_task_list_endpoint_returns_a_list() -> None:
    response = TestClient(app).get("/api/v1/tasks")

    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_review_types_endpoint_returns_enabled_platform_types() -> None:
    response = TestClient(app).get("/api/v1/review-types")

    assert response.status_code == 200
    assert response.json()[0]["review_type_id"] == "technical-architecture"


def test_approval_rule_endpoints_expose_markdown_document_and_outline() -> None:
    client = TestClient(app)

    catalog = client.get("/api/v1/approval-rules")
    document = client.get("/api/v1/approval-rules/technical-architecture")
    overview_document = client.get("/api/v1/approval-rules/overview-design")

    assert catalog.status_code == 200
    assert [item["rule_id"] for item in catalog.json()] == [
        "technical-architecture",
        "overview-design",
    ]
    assert document.status_code == 200
    assert document.json()["title"] == "技术架构报告审核"
    assert document.json()["outline"][0]["title"] == "技术架构报告审核"
    assert 'id="rule-technical-architecture-1"' in document.json()["html"]
    assert overview_document.status_code == 200
    assert overview_document.json()["title"] == "概要设计审核"
    assert 'id="rule-overview-design-1"' in overview_document.json()["html"]
