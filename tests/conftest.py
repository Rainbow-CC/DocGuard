import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from docguard.services.profiles import ReviewTypeRegistry


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INIT_SCRIPT = PROJECT_ROOT / "init" / "apply_sql.py"
TEST_APP_DATABASE_PATH = Path(tempfile.gettempdir()) / f"docguard-pytest-{os.getpid()}.sqlite3"


def _provision_database(database_path: Path) -> None:
    subprocess.run(
        [sys.executable, str(INIT_SCRIPT), "--database-path", str(database_path)],
        check=True,
        capture_output=True,
        text=True,
    )


# Tests provision their application database through the same operations entry
# point used in deployment, before any test module imports the ASGI app.
os.environ["DOCGUARD_DATABASE_PATH"] = str(TEST_APP_DATABASE_PATH)
_provision_database(TEST_APP_DATABASE_PATH)


@pytest.fixture
def review_type_database_path(tmp_path: Path) -> Path:
    return tmp_path / "review-types.sqlite3"


@pytest.fixture
def review_type_registry(review_type_database_path: Path) -> ReviewTypeRegistry:
    _provision_database(review_type_database_path)
    return ReviewTypeRegistry(review_type_database_path)


@pytest.fixture
def provision_database():
    return _provision_database


@pytest.fixture
def technical_review_type(review_type_registry: ReviewTypeRegistry):
    return review_type_registry.get("technical-architecture")
