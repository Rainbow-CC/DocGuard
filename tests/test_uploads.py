from hashlib import sha256
from pathlib import PurePosixPath

from fastapi.testclient import TestClient

from docguard.api.app import app
from docguard.services.uploads import UploadStorage


def _client(tmp_path, max_upload_bytes: int = 100) -> TestClient:
    app.state.upload_storage = UploadStorage(
        write_root=tmp_path / "uploads",
        agent_root=PurePosixPath("/home/ubuntu/docguard-inbox"),
        max_upload_bytes=max_upload_bytes,
    )
    return TestClient(app)


def test_upload_docx_stores_under_agent_and_returns_linux_path(tmp_path) -> None:
    content = b"PK\x03\x04minimal-docx"
    response = _client(tmp_path).post(
        "/api/v1/agents/reviewer/uploads",
        files={
            "file": (
                "architecture.docx",
                content,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["agent_id"] == "reviewer"
    assert body["filename"] == "architecture.docx"
    assert body["size_bytes"] == len(content)
    assert body["content_sha256"] == sha256(content).hexdigest()
    assert body["agent_path"] == f"/home/ubuntu/docguard-inbox/reviewer/{body['upload_id']}/source.docx"
    assert body["source_uri"] == f"file://{body['agent_path']}"
    assert (tmp_path / "uploads" / "reviewer" / body["upload_id"] / "source.docx").read_bytes() == content


def test_upload_rejects_path_traversal_filename_and_does_not_write_file(tmp_path) -> None:
    response = _client(tmp_path).post(
        "/api/v1/agents/reviewer/uploads",
        files={"file": ("../not-a-document.docx", b"PK\x03\x04minimal-docx", "application/octet-stream")},
    )

    assert response.status_code == 422
    assert not (tmp_path / "uploads").exists()


def test_upload_rejects_oversized_docx_and_removes_partial_file(tmp_path) -> None:
    response = _client(tmp_path, max_upload_bytes=4).post(
        "/api/v1/agents/reviewer/uploads",
        files={"file": ("large.docx", b"PK\x03\x04too-large", "application/octet-stream")},
    )

    assert response.status_code == 413
    assert list((tmp_path / "uploads" / "reviewer").rglob(".source.uploading")) == []

