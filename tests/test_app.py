import importlib

import pytest

import database.db as db


@pytest.mark.parametrize("path", ["/", "/register", "/login"])
def test_pages_render(client, path):
    response = client.get(path)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Spendly" in html
    assert "Track every rupee" in html  # footer from base.html


@pytest.mark.parametrize("path, text", [
    ("/profile", "Profile page — coming in Step 4"),
    ("/expenses/add", "Add expense — coming in Step 7"),
    ("/expenses/1/edit", "Edit expense — coming in Step 8"),
    ("/expenses/1/delete", "Delete expense — coming in Step 9"),
])
def test_placeholder_routes_unchanged(client, path, text):
    response = client.get(path)
    assert response.status_code == 200
    assert response.get_data(as_text=True) == text


def test_unknown_route_404(client):
    assert client.get("/nope").status_code == 404


def test_startup_creates_and_seeds_database(db_path):
    import os

    import app as app_module

    assert not os.path.exists(db_path)
    importlib.reload(app_module)  # re-run the module-level startup block against the fresh temp DB
    assert os.path.exists(db_path)

    c = db.get_db()
    assert c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    assert c.execute("SELECT COUNT(*) FROM expenses").fetchone()[0] == 8
    c.close()

    importlib.reload(app_module)  # a second startup must not duplicate data
    c = db.get_db()
    assert c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    assert c.execute("SELECT COUNT(*) FROM expenses").fetchone()[0] == 8
    c.close()
