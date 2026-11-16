import os
import tempfile

import pytest

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/test.db"
os.environ["SECRET_KEY"] = "test-secret-key-test-secret-key-0123"
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["ADMIN_PASSWORD"] = "admin-password"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def anon():
    """Отдельный клиент без cookie: сессионный client делит их между тестами."""
    with TestClient(app) as c:
        yield c


@pytest.fixture
def admin(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin-password"})
    assert r.status_code == 200, r.text
    return client


@pytest.fixture(autouse=True)
def _reset_login_throttle():
    # Счётчик неудачных входов живёт в памяти процесса и иначе копится между тестами.
    from app.routers import auth

    auth._failures.clear()
    yield
    auth._failures.clear()
