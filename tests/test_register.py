import importlib
import os
import re
import sqlite3
import threading

import pytest
from werkzeug.security import check_password_hash

import database.db as db

SUCCESS_MESSAGE = "Account created. Please sign in."
DUPLICATE_MESSAGE = "An account with this email already exists."

VALID = {"name": "Rahul Mishra", "email": "rahul@example.com", "password": "secret1"}


def users():
    c = db.get_db()
    try:
        return c.execute("SELECT * FROM users ORDER BY id").fetchall()
    finally:
        c.close()


def user_by_email(email):
    c = db.get_db()
    try:
        return c.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    finally:
        c.close()


def form(**overrides):
    return {**VALID, **overrides}


def email_of_length(n):
    domain = "@example.com"
    return "a" * (n - len(domain)) + domain


# ------------------------------------------------------------------ #
# create_user()                                                       #
# ------------------------------------------------------------------ #

def test_create_user_inserts_row_and_returns_id():
    db.init_db()
    new_id = db.create_user("Asha Rao", "asha@example.com", "hash-value")

    rows = users()
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == new_id
    assert row["name"] == "Asha Rao"
    assert row["email"] == "asha@example.com"
    assert row["password_hash"] == "hash-value"
    assert row["created_at"]


def test_create_user_ids_increase():
    db.init_db()
    first = db.create_user("A", "a@example.com", "h")
    second = db.create_user("B", "b@example.com", "h")
    assert second > first


@pytest.mark.parametrize("duplicate", ["asha@example.com", "ASHA@Example.COM"])
def test_create_user_duplicate_email_raises(duplicate):
    db.init_db()
    db.create_user("Asha Rao", "asha@example.com", "h")
    with pytest.raises(sqlite3.IntegrityError):
        db.create_user("Someone Else", duplicate, "h")
    assert len(users()) == 1


def test_create_user_closes_connection(db_path):
    db.init_db()
    db.create_user("Asha Rao", "asha@example.com", "h")
    with pytest.raises(sqlite3.IntegrityError):
        db.create_user("Asha Rao", "asha@example.com", "h")
    # Windows refuses to delete a file that still has an open handle.
    os.remove(db_path)
    assert not os.path.exists(db_path)


def test_create_user_concurrent_same_email():
    db.init_db()
    barrier = threading.Barrier(2)
    results = []

    def worker(name):
        barrier.wait()
        try:
            results.append(("ok", db.create_user(name, "race@example.com", "h")))
        except sqlite3.IntegrityError:
            results.append(("duplicate", None))
        except Exception as exc:  # anything else is a test failure
            results.append(("error", exc))

    threads = [threading.Thread(target=worker, args=(f"User {i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(kind for kind, _ in results) == ["duplicate", "ok"]
    assert len(users()) == 1


# ------------------------------------------------------------------ #
# Success path                                                        #
# ------------------------------------------------------------------ #

def test_valid_signup_redirects_to_login_and_creates_one_user(client):
    assert len(users()) == 1  # the seeded demo user

    response = client.post("/register", data=VALID)

    assert response.status_code == 302
    assert response.headers["Location"] == "/login"
    assert len(users()) == 2
    row = user_by_email("rahul@example.com")
    assert row["name"] == "Rahul Mishra"


def test_password_is_stored_as_werkzeug_hash(client):
    client.post("/register", data=VALID)
    stored = user_by_email("rahul@example.com")["password_hash"]

    assert stored != VALID["password"]
    assert VALID["password"] not in stored
    assert stored.startswith("scrypt:")
    assert check_password_hash(stored, VALID["password"])
    assert not check_password_hash(stored, "secret2")


def test_confirmation_shows_once_on_login(client):
    response = client.post("/register", data=VALID, follow_redirects=True)
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert response.request.path == "/login"
    assert f'<div class="auth-success">{SUCCESS_MESSAGE}</div>' in html

    again = client.get("/login").get_data(as_text=True)
    assert SUCCESS_MESSAGE not in again
    assert "auth-success" not in again
    assert len(users()) == 2


def test_email_and_name_are_normalised(client):
    response = client.post(
        "/register",
        data=form(name="  Rahul Mishra  ", email=" Rahul@Example.COM "),
    )
    assert response.status_code == 302
    row = users()[-1]
    assert row["email"] == "rahul@example.com"
    assert row["name"] == "Rahul Mishra"


def test_signup_does_not_sign_the_user_in(client):
    client.post("/register", data=VALID)
    with client.session_transaction() as sess:
        assert set(sess.keys()) <= {"_flashes"}
        assert "user_id" not in sess

    assert client.get("/profile").get_data(as_text=True) == "Profile page — coming in Step 4"


def test_password_is_not_trimmed(client):
    password = "  pass word  "
    assert client.post("/register", data=form(password=password)).status_code == 302
    stored = user_by_email("rahul@example.com")["password_hash"]
    assert check_password_hash(stored, password)
    assert not check_password_hash(stored, password.strip())


def test_whitespace_only_password_is_accepted(client):
    assert client.post("/register", data=form(password=" " * 6)).status_code == 302
    assert check_password_hash(user_by_email("rahul@example.com")["password_hash"], " " * 6)


@pytest.mark.parametrize("data", [
    form(name="N" * 100),
    form(email=email_of_length(254)),
    form(password="123456"),
    form(password="p" * 128),
    form(name="राहुल मिश्रा", email="राहुल@example.com"),
    form(email="first.last+tag@mail.example.co.in"),
])
def test_boundary_values_are_accepted(client, data):
    assert client.post("/register", data=data).status_code == 302
    assert len(users()) == 2


# ------------------------------------------------------------------ #
# Validation                                                          #
# ------------------------------------------------------------------ #

NAME_REQUIRED = "Please enter your name."
NAME_TOO_LONG = "Name must be 100 characters or fewer."
EMAIL_REQUIRED = "Please enter your email address."
EMAIL_TOO_LONG = "Email must be 254 characters or fewer."
EMAIL_INVALID = "Please enter a valid email address."
PASSWORD_TOO_SHORT = "Password must be at least 6 characters."
PASSWORD_TOO_LONG = "Password must be 128 characters or fewer."


def without(field):
    return {k: v for k, v in VALID.items() if k != field}


@pytest.mark.parametrize("data, message", [
    (form(name=""), NAME_REQUIRED),
    (form(name="   \t "), NAME_REQUIRED),
    (without("name"), NAME_REQUIRED),
    (form(name="N" * 101), NAME_TOO_LONG),
    (form(email=""), EMAIL_REQUIRED),
    (form(email="    "), EMAIL_REQUIRED),
    (without("email"), EMAIL_REQUIRED),
    (form(email=email_of_length(255)), EMAIL_TOO_LONG),
    (form(email="rahul"), EMAIL_INVALID),
    (form(email="rahul@"), EMAIL_INVALID),
    (form(email="@example.com"), EMAIL_INVALID),
    (form(email="a@b"), EMAIL_INVALID),
    (form(email="a@.com"), EMAIL_INVALID),
    (form(email="a@b."), EMAIL_INVALID),
    (form(email="a@b..com"), EMAIL_INVALID),
    (form(email="a@@b.com"), EMAIL_INVALID),
    (form(email="a@b@c.com"), EMAIL_INVALID),
    (form(email="a b@c.com"), EMAIL_INVALID),
    (form(email="a@b\tc.com"), EMAIL_INVALID),
    (form(password=""), PASSWORD_TOO_SHORT),
    (without("password"), PASSWORD_TOO_SHORT),
    (form(password="12345"), PASSWORD_TOO_SHORT),
    (form(password="p" * 129), PASSWORD_TOO_LONG),
])
def test_invalid_input_is_rejected(client, data, message):
    response = client.post("/register", data=data)
    html = response.get_data(as_text=True)

    assert response.status_code == 400
    assert f'<div class="auth-error">{message}</div>' in html
    assert len(users()) == 1


def test_empty_post_is_a_form_error_not_a_server_error(client):
    response = client.post("/register")
    assert response.status_code == 400
    assert NAME_REQUIRED in response.get_data(as_text=True)
    assert len(users()) == 1


def test_only_the_first_error_is_shown(client):
    response = client.post("/register", data={"name": "", "email": "bad", "password": "1"})
    html = response.get_data(as_text=True)

    assert response.status_code == 400
    assert html.count('class="auth-error"') == 1
    assert NAME_REQUIRED in html
    assert EMAIL_INVALID not in html
    assert PASSWORD_TOO_SHORT not in html


def test_error_order_is_name_then_email_then_password(client):
    html = client.post(
        "/register", data={"name": "Rahul", "email": "bad", "password": "1"}
    ).get_data(as_text=True)
    assert EMAIL_INVALID in html
    assert PASSWORD_TOO_SHORT not in html


# ------------------------------------------------------------------ #
# Duplicate email                                                     #
# ------------------------------------------------------------------ #

@pytest.mark.parametrize("email", ["demo@spendly.com", "DEMO@spendly.com", "  Demo@Spendly.com  "])
def test_existing_email_is_rejected_in_any_case(client, email):
    response = client.post("/register", data=form(email=email))
    html = response.get_data(as_text=True)

    assert response.status_code == 409
    assert f'<div class="auth-error">{DUPLICATE_MESSAGE}</div>' in html
    assert len(users()) == 1


def test_registering_twice_creates_one_user(client):
    assert client.post("/register", data=VALID).status_code == 302
    second = client.post("/register", data=form(name="Other Person", password="different1"))

    assert second.status_code == 409
    assert DUPLICATE_MESSAGE in second.get_data(as_text=True)
    assert len(users()) == 2
    assert user_by_email("rahul@example.com")["name"] == "Rahul Mishra"


def test_non_ascii_duplicate_is_rejected(client):
    # NOCASE only folds ASCII; lowercasing in the route covers the rest.
    assert client.post("/register", data=form(email="RÄHUL@example.com")).status_code == 302
    assert client.post("/register", data=form(email="rähul@example.com")).status_code == 409
    assert len(users()) == 2


# ------------------------------------------------------------------ #
# Re-fill and escaping                                                #
# ------------------------------------------------------------------ #

def test_form_keeps_name_and_email_but_never_the_password(client):
    password = "Zx9!q"  # too short, so the form comes back
    html = client.post(
        "/register", data=form(name="Rahul Mishra", email="Rahul@Example.com", password=password)
    ).get_data(as_text=True)

    assert 'value="Rahul Mishra"' in html
    assert 'value="Rahul@Example.com"' in html  # as typed, not lowercased
    assert password not in html


def test_duplicate_error_also_keeps_fields_and_drops_password(client):
    html = client.post(
        "/register", data=form(email="demo@spendly.com", password="Sup3rSecret!")
    ).get_data(as_text=True)

    assert 'value="Rahul Mishra"' in html
    assert 'value="demo@spendly.com"' in html
    assert "Sup3rSecret!" not in html


def test_sql_in_input_is_stored_as_plain_text(client):
    name = "'; DROP TABLE users; --"
    email = "x'or'1'='1@example.com"
    assert client.post("/register", data=form(name=name, email=email)).status_code == 302

    rows = users()
    assert len(rows) == 2
    assert rows[0]["email"] == "demo@spendly.com"
    assert rows[1]["name"] == name
    assert rows[1]["email"] == email


def test_html_in_input_is_escaped_when_redisplayed(client):
    name = '<script>alert(1)</script>'
    email = '"><img src=x onerror=alert(1)>'
    html = client.post("/register", data=form(name=name, email=email)).get_data(as_text=True)

    assert name not in html
    assert "<img" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&#34;&gt;&lt;img" in html


def test_html_in_name_is_stored_verbatim(client):
    name = "<b>Rahul</b>"
    assert client.post("/register", data=form(name=name)).status_code == 302
    assert user_by_email("rahul@example.com")["name"] == name


# ------------------------------------------------------------------ #
# Regression and configuration                                        #
# ------------------------------------------------------------------ #

def test_register_page_markup(client):
    response = client.get("/register")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'action="/register"' in html
    assert 'placeholder="Min. 6 characters"' in html
    assert "Min. 8 characters" not in html
    assert 'minlength="6"' in html
    assert html.count('value=""') == 2  # name and email start empty
    assert "auth-error" not in html


def test_login_page_has_no_confirmation_by_default(client):
    html = client.get("/login").get_data(as_text=True)
    assert "auth-success" not in html
    assert SUCCESS_MESSAGE not in html


def test_secret_key_comes_from_environment_with_fallback(monkeypatch):
    import app as app_module

    monkeypatch.setenv("SECRET_KEY", "from-the-environment")
    importlib.reload(app_module)
    assert app_module.app.secret_key == "from-the-environment"

    monkeypatch.setenv("SECRET_KEY", "")
    importlib.reload(app_module)
    assert app_module.app.secret_key == "spendly-dev-secret-key"

    monkeypatch.delenv("SECRET_KEY")
    importlib.reload(app_module)
    assert app_module.app.secret_key == "spendly-dev-secret-key"


def test_auth_success_style_uses_only_css_variables():
    css_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "css", "style.css")
    with open(css_path, encoding="utf-8") as f:
        css = f.read()

    rule = re.search(r"\.auth-success\s*\{([^}]*)\}", css)
    assert rule is not None
    body = rule.group(1)
    assert "#" not in body
    assert "var(--accent-light)" in body
    assert "var(--accent)" in body
