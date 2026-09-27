import os

import pytest

import database.db as db

REAL_DB_PATH = db.DB_PATH


def _snapshot(path):
    if not os.path.exists(path):
        return None
    st = os.stat(path)
    return (st.st_size, st.st_mtime_ns)


@pytest.fixture(scope="session", autouse=True)
def real_db_untouched():
    """Fail the run if any test creates or modifies the real expense_tracker.db."""
    before = _snapshot(REAL_DB_PATH)
    yield
    assert _snapshot(REAL_DB_PATH) == before, "tests touched the real database file"


@pytest.fixture(autouse=True)
def db_path(tmp_path, monkeypatch):
    """Point every test at its own throwaway database file."""
    path = str(tmp_path / "test.db")
    monkeypatch.setattr(db, "DB_PATH", path)
    return path


@pytest.fixture
def app(db_path):
    # Imported lazily so the module-level init_db()/seed_db() runs against the temp DB.
    import app as app_module

    app_module.app.config.update(TESTING=True)
    db.init_db()
    db.seed_db()
    return app_module.app


@pytest.fixture
def conn(db_path):
    db.init_db()
    connection = db.get_db()
    yield connection
    connection.close()


@pytest.fixture
def user_id(conn):
    cursor = conn.execute(
        "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
        ("Test User", "test@spendly.com", "hash"),
    )
    conn.commit()
    return cursor.lastrowid
