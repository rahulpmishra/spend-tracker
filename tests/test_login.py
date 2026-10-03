import os
import re

import pytest

import database.db as db

DEMO_EMAIL = "demo@spendly.com"
DEMO_PASSWORD = "demo123"

LOGIN_FAILED = "Invalid email or password."
EMAIL_REQUIRED = "Please enter your email address."
PASSWORD_REQUIRED = "Please enter your password."
SIGNED_OUT_MESSAGE = "You have been signed out."
SIGNUP_MESSAGE = "Account created. Please sign in."

OTHER = {"name": "Rahul Mishra", "email": "rahul@example.com", "password": "secret1"}


def users():
    c = db.get_db()
    try:
        return c.execute("SELECT * FROM users ORDER BY id").fetchall()
    finally:
        c.close()


def demo_id():
    return db.get_user_by_email(DEMO_EMAIL)["id"]


def sign_in(client, email=DEMO_EMAIL, password=DEMO_PASSWORD):
    return client.post("/login", data={"email": email, "password": password})


def register(client, **overrides):
    """Create an account through the real route and return its id."""
    data = {**OTHER, **overrides}
    assert client.post("/register", data=data).status_code == 302
    return db.get_user_by_email(data["email"].strip().lower())["id"]


def session_of(client):
    with client.session_transaction() as sess:
        return dict(sess)


def navbar(html):
    return re.search(r'<nav class="navbar">(.*?)</nav>', html, re.DOTALL).group(1)


def error_div(message):
    return f'<div class="auth-error">{message}</div>'


def email_of_length(n):
    domain = "@example.com"
    return "a" * (n - len(domain)) + domain


def assert_signed_out_navbar(html):
    nav = navbar(html)
    assert "Sign in" in nav
    assert "Get started" in nav
    assert "nav-user" not in nav
    assert "Sign out" not in nav


@pytest.fixture
def hash_checks(app, monkeypatch):
    """Record the stored hash passed to every check_password_hash call made by the login route."""
    import app as app_module

    calls = []
    real = app_module.check_password_hash

    def spy(pwhash, password):
        calls.append(pwhash)
        return real(pwhash, password)

    monkeypatch.setattr(app_module, "check_password_hash", spy)
    return calls


# ------------------------------------------------------------------ #
# get_user_by_email() / get_user_by_id()                              #
# ------------------------------------------------------------------ #

def test_get_user_by_email_returns_row(app):
    row = db.get_user_by_email(DEMO_EMAIL)
    assert set(row.keys()) == {"id", "name", "email", "password_hash"}
    assert row["name"] == "Demo User"
    assert row["email"] == DEMO_EMAIL
    assert row["password_hash"].startswith("scrypt:")


def test_get_user_by_email_unknown_returns_none(app):
    assert db.get_user_by_email("nobody@example.com") is None
    assert db.get_user_by_email("") is None


def test_get_user_by_email_ignores_ascii_case(app):
    assert db.get_user_by_email("DEMO@Spendly.com")["id"] == demo_id()


def test_get_user_by_id_returns_row_without_hash(app):
    row = db.get_user_by_id(demo_id())
    assert set(row.keys()) == {"id", "name", "email"}
    assert row["name"] == "Demo User"


def test_get_user_by_id_unknown_returns_none(app):
    assert db.get_user_by_id(999999) is None


def test_lookups_treat_sql_as_plain_text(app):
    assert db.get_user_by_email("' OR '1'='1") is None
    assert db.get_user_by_email("x'; DROP TABLE users; --") is None
    assert db.get_user_by_id("1 OR 1=1") is None
    assert len(users()) == 1


def test_lookups_close_their_connections(db_path):
    db.init_db()
    db.create_user("Asha Rao", "asha@example.com", "h")
    db.get_user_by_email("asha@example.com")
    db.get_user_by_email("nobody@example.com")
    db.get_user_by_id(1)
    db.get_user_by_id(999999)
    # Windows refuses to delete a file that still has an open handle.
    os.remove(db_path)
    assert not os.path.exists(db_path)


# ------------------------------------------------------------------ #
# Sign-in success                                                     #
# ------------------------------------------------------------------ #

def test_demo_user_can_sign_in(client):
    response = sign_in(client)

    assert response.status_code == 302
    assert response.headers["Location"] == "/"
    assert session_of(client) == {"user_id": demo_id()}


def test_sign_in_lands_on_the_landing_page(client):
    response = client.post(
        "/login", data={"email": DEMO_EMAIL, "password": DEMO_PASSWORD}, follow_redirects=True
    )
    assert response.status_code == 200
    assert response.request.path == "/"


@pytest.mark.parametrize("email", [" DEMO@Spendly.COM ", "Demo@spendly.com", "\tdemo@spendly.com\n"])
def test_email_is_trimmed_and_case_insensitive(client, email):
    assert sign_in(client, email=email).status_code == 302
    assert session_of(client) == {"user_id": demo_id()}


def test_registered_account_can_sign_in(client):
    new_id = register(client)
    response = sign_in(client, OTHER["email"], OTHER["password"])

    assert response.status_code == 302
    assert session_of(client) == {"user_id": new_id}
    assert new_id != demo_id()


@pytest.mark.parametrize("email", ["rähul@example.com", "RÄHUL@example.com"])
def test_non_ascii_email_signs_in_in_any_case(client, email):
    # NOCASE only folds ASCII; lowercasing in the route covers the rest.
    new_id = register(client, email="RÄHUL@example.com")
    assert sign_in(client, email, OTHER["password"]).status_code == 302
    assert session_of(client) == {"user_id": new_id}


def test_password_with_spaces_needs_the_exact_string(client):
    password = "  pass word  "
    new_id = register(client, password=password)

    assert sign_in(client, OTHER["email"], password.strip()).status_code == 401
    assert "user_id" not in session_of(client)
    assert sign_in(client, OTHER["email"], password).status_code == 302
    assert session_of(client) == {"user_id": new_id}


def test_session_cookie_flags(client):
    response = sign_in(client)
    cookies = [c for c in response.headers.getlist("Set-Cookie") if c.startswith("session=")]

    assert len(cookies) == 1
    cookie = cookies[0]
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie
    assert "Expires" not in cookie  # ends with the browser session
    assert "Max-Age" not in cookie


def test_session_holds_no_password_or_hash(app, client):
    sign_in(client)
    value = client.get_cookie("session").value
    data = app.session_interface.get_signing_serializer(app).loads(value)

    assert data == {"user_id": demo_id()}
    assert DEMO_PASSWORD not in value
    assert "scrypt" not in value


# ------------------------------------------------------------------ #
# Sign-in failure                                                     #
# ------------------------------------------------------------------ #

@pytest.mark.parametrize("email, password", [
    (DEMO_EMAIL, "wrong-password"),
    ("nobody@example.com", DEMO_PASSWORD),
    (DEMO_EMAIL, "demo123 "),
    (DEMO_EMAIL, " demo123"),
    (DEMO_EMAIL, "DEMO123"),
    (DEMO_EMAIL, "      "),  # whitespace-only is a password, not an empty field
    pytest.param(DEMO_EMAIL, "p" * 128, id="password-128"),
    pytest.param(DEMO_EMAIL, "p" * 129, id="password-129"),
    # Short id: the test name goes into an environment variable, which Windows caps at 32767 characters.
    pytest.param(DEMO_EMAIL, "p" * 100_000, id="password-100000"),
    pytest.param(email_of_length(255), DEMO_PASSWORD, id="email-255"),
    ("not-an-email", DEMO_PASSWORD),
    ("' OR '1'='1", DEMO_PASSWORD),
    ("demo@spendly.com' --", DEMO_PASSWORD),
    ("demo@spendly.com' OR '1'='1", "x"),
    (DEMO_EMAIL, "' OR '1'='1"),
    ("demo@spendly.com%", DEMO_PASSWORD),
    ("%", DEMO_PASSWORD),
])
def test_wrong_credentials_are_rejected(client, email, password):
    response = sign_in(client, email, password)
    html = response.get_data(as_text=True)

    assert response.status_code == 401
    assert html.count('class="auth-error"') == 1
    assert error_div(LOGIN_FAILED) in html
    assert session_of(client) == {}
    assert_signed_out_navbar(html)
    assert len(users()) == 1


@pytest.mark.parametrize("data, message", [
    ({"email": "", "password": DEMO_PASSWORD}, EMAIL_REQUIRED),
    ({"email": "   \t ", "password": DEMO_PASSWORD}, EMAIL_REQUIRED),
    ({"password": DEMO_PASSWORD}, EMAIL_REQUIRED),
    ({"email": DEMO_EMAIL, "password": ""}, PASSWORD_REQUIRED),
    ({"email": DEMO_EMAIL}, PASSWORD_REQUIRED),
    ({"email": "nobody@example.com", "password": ""}, PASSWORD_REQUIRED),
    ({"email": "", "password": ""}, EMAIL_REQUIRED),
    ({}, EMAIL_REQUIRED),
])
def test_empty_fields_get_their_own_message(client, data, message):
    response = client.post("/login", data=data)
    html = response.get_data(as_text=True)

    assert response.status_code == 400
    assert html.count('class="auth-error"') == 1
    assert error_div(message) in html
    assert session_of(client) == {}


def test_unknown_email_and_wrong_password_look_the_same(client):
    wrong_password = sign_in(client, "demo@spendly.com", "wrong-password")
    unknown_email = sign_in(client, "nemo@spendly.com", "wrong-password")

    assert wrong_password.status_code == unknown_email.status_code == 401
    # The pages differ only in the email that was typed and re-filled.
    assert wrong_password.get_data(as_text=True).replace("demo@", "X@") == \
        unknown_email.get_data(as_text=True).replace("nemo@", "X@")


def test_malformed_stored_hash_is_a_failed_login_not_a_server_error(client):
    db.create_user("Broken", "broken@example.com", "hash")
    response = sign_in(client, "broken@example.com", "hash")

    assert response.status_code == 401
    assert error_div(LOGIN_FAILED) in response.get_data(as_text=True)
    assert session_of(client) == {}


def test_failed_attempt_keeps_an_existing_anonymous_session_unsigned(client):
    assert sign_in(client, DEMO_EMAIL, "wrong-password").status_code == 401
    assert sign_in(client, DEMO_EMAIL, "wrong-password").status_code == 401
    assert "user_id" not in session_of(client)
    assert sign_in(client).status_code == 302  # no lockout after failures


# ------------------------------------------------------------------ #
# Timing guard: the hash check runs for unknown emails too            #
# ------------------------------------------------------------------ #

def test_dummy_hash_is_a_real_scrypt_hash(app):
    import app as app_module

    assert app_module._DUMMY_HASH.startswith("scrypt:")
    stored = db.get_user_by_email(DEMO_EMAIL)["password_hash"]
    # Same method and cost parameters as real hashes, so the work is the same.
    assert app_module._DUMMY_HASH.split("$")[0] == stored.split("$")[0]


def test_unknown_email_still_runs_one_hash_check(client, hash_checks):
    import app as app_module

    sign_in(client, "nobody@example.com", "whatever")
    assert hash_checks == [app_module._DUMMY_HASH]


def test_known_email_runs_one_hash_check_against_its_own_hash(client, hash_checks):
    stored = db.get_user_by_email(DEMO_EMAIL)["password_hash"]

    sign_in(client, DEMO_EMAIL, "wrong-password")
    assert hash_checks == [stored]


def test_dummy_password_does_not_sign_anyone_in(client):
    assert sign_in(client, "nobody@example.com", "spendly-dummy-password").status_code == 401
    assert session_of(client) == {}


@pytest.mark.parametrize("data", [
    {"email": "", "password": DEMO_PASSWORD},
    {"email": DEMO_EMAIL, "password": ""},
    {"email": email_of_length(255), "password": DEMO_PASSWORD},
    {"email": DEMO_EMAIL, "password": "p" * 129},
])
def test_empty_or_over_length_input_is_never_hashed(client, hash_checks, data):
    client.post("/login", data=data)
    assert hash_checks == []


# ------------------------------------------------------------------ #
# Re-fill and escaping                                                #
# ------------------------------------------------------------------ #

def test_form_keeps_the_email_but_never_the_password(client):
    password = "Sup3rSecret!"
    html = sign_in(client, "Demo@Spendly.com", password).get_data(as_text=True)

    assert 'value="Demo@Spendly.com"' in html  # as typed, not lowercased
    assert password not in html


def test_email_is_refilled_after_an_empty_password(client):
    html = client.post("/login", data={"email": "rahul@example.com", "password": ""}).get_data(as_text=True)
    assert 'value="rahul@example.com"' in html


def test_html_in_email_is_escaped_when_redisplayed(client):
    email = '"><script>alert(1)</script>'
    html = sign_in(client, email, "whatever").get_data(as_text=True)

    assert "<script>alert(1)</script>" not in html
    assert "&#34;&gt;&lt;script&gt;alert(1)&lt;/script&gt;" in html


# ------------------------------------------------------------------ #
# Session replacement and the signed-in guard                         #
# ------------------------------------------------------------------ #

def test_sign_in_replaces_whatever_was_in_the_session(client):
    with client.session_transaction() as sess:
        sess["junk"] = "left over"
        sess["cart"] = [1, 2, 3]

    assert sign_in(client).status_code == 302
    assert session_of(client) == {"user_id": demo_id()}


@pytest.mark.parametrize("method, path, data", [
    ("get", "/login", None),
    ("post", "/login", {"email": OTHER["email"], "password": OTHER["password"]}),
    ("post", "/login", {"email": "", "password": ""}),
    ("get", "/register", None),
    ("post", "/register", {"name": "New Person", "email": "new@example.com", "password": "secret1"}),
])
def test_signed_in_user_is_sent_away_from_login_and_register(client, method, path, data):
    register(client)
    sign_in(client)
    before = len(users())

    response = getattr(client, method)(path, data=data)

    assert response.status_code == 302
    assert response.headers["Location"] == "/"
    assert session_of(client) == {"user_id": demo_id()}  # still the first user
    assert len(users()) == before


# ------------------------------------------------------------------ #
# Navbar                                                              #
# ------------------------------------------------------------------ #

@pytest.mark.parametrize("path", ["/", "/login", "/register"])
def test_navbar_when_signed_out(client, path):
    html = client.get(path).get_data(as_text=True)
    assert_signed_out_navbar(html)
    assert 'href="/login"' in navbar(html)
    assert 'href="/register"' in navbar(html)


def test_navbar_when_signed_in(client):
    sign_in(client)
    nav = navbar(client.get("/").get_data(as_text=True))

    assert '<span class="nav-user">Demo User</span>' in nav
    assert '<a href="/logout" class="nav-cta">Sign out</a>' in nav
    assert "Sign in" not in nav
    assert "Get started" not in nav
    assert 'href="/login"' not in nav
    assert 'href="/register"' not in nav


def test_navbar_shows_the_right_user(client):
    register(client)
    sign_in(client, OTHER["email"], OTHER["password"])
    nav = navbar(client.get("/").get_data(as_text=True))

    assert '<span class="nav-user">Rahul Mishra</span>' in nav
    assert "Demo User" not in nav


def test_navbar_escapes_the_name(client):
    name = "<script>alert(1)</script>"
    register(client, name=name)
    sign_in(client, OTHER["email"], OTHER["password"])
    html = client.get("/").get_data(as_text=True)

    assert name not in html
    assert '<span class="nav-user">&lt;script&gt;alert(1)&lt;/script&gt;</span>' in html


def test_navbar_renders_a_long_name_in_full(client):
    name = "N" * 100
    register(client, name=name)
    sign_in(client, OTHER["email"], OTHER["password"])

    # Truncation is done by CSS; the markup carries the whole name.
    assert f'<span class="nav-user">{name}</span>' in client.get("/").get_data(as_text=True)


def test_navbar_shows_non_ascii_name(client):
    register(client, name="राहुल मिश्रा")
    sign_in(client, OTHER["email"], OTHER["password"])
    assert '<span class="nav-user">राहुल मिश्रा</span>' in client.get("/").get_data(as_text=True)


# ------------------------------------------------------------------ #
# Logout                                                              #
# ------------------------------------------------------------------ #

def test_logout_clears_the_session_and_redirects_to_login(client):
    sign_in(client)
    response = client.get("/logout")

    assert response.status_code == 302
    assert response.headers["Location"] == "/login"
    assert "user_id" not in session_of(client)


def test_logout_message_shows_once(client):
    sign_in(client)
    response = client.get("/logout", follow_redirects=True)
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert response.request.path == "/login"
    assert f'<div class="auth-success">{SIGNED_OUT_MESSAGE}</div>' in html
    assert_signed_out_navbar(html)

    again = client.get("/login").get_data(as_text=True)
    assert SIGNED_OUT_MESSAGE not in again
    assert "auth-success" not in again
    assert session_of(client) == {}


def test_navbar_is_signed_out_after_logout(client):
    sign_in(client)
    client.get("/logout")
    assert_signed_out_navbar(client.get("/").get_data(as_text=True))


def test_logout_without_a_session_is_harmless(client):
    response = client.get("/logout")

    assert response.status_code == 302
    assert response.headers["Location"] == "/login"
    assert session_of(client) == {}  # no message queued either

    html = client.get("/login").get_data(as_text=True)
    assert SIGNED_OUT_MESSAGE not in html


def test_logout_twice_is_harmless(client):
    sign_in(client)
    assert client.get("/logout").status_code == 302
    assert client.get("/logout").status_code == 302
    assert "user_id" not in session_of(client)


def test_logout_drops_everything_in_the_session(client):
    sign_in(client)
    with client.session_transaction() as sess:
        sess["extra"] = "value"

    client.get("/logout")
    assert set(session_of(client)) <= {"_flashes"}


def test_logout_is_get_only(client):
    sign_in(client)
    assert client.post("/logout").status_code == 405
    assert session_of(client) == {"user_id": demo_id()}


def test_sign_in_again_after_logout(client):
    sign_in(client)
    client.get("/logout")
    assert sign_in(client).status_code == 302
    assert session_of(client) == {"user_id": demo_id()}


def test_switching_users_through_logout(client):
    other_id = register(client)
    sign_in(client)
    client.get("/logout")
    sign_in(client, OTHER["email"], OTHER["password"])

    assert session_of(client) == {"user_id": other_id}
    assert "Rahul Mishra" in navbar(client.get("/").get_data(as_text=True))


def test_copied_cookie_still_works_after_logout(client):
    # Known limitation: Flask sessions live in the cookie, so sign-out cannot revoke a copy.
    sign_in(client)
    copied = client.get_cookie("session").value
    client.get("/logout")
    assert_signed_out_navbar(client.get("/").get_data(as_text=True))

    client.set_cookie("session", copied)
    assert "Sign out" in navbar(client.get("/").get_data(as_text=True))


# ------------------------------------------------------------------ #
# Stale, malformed and forged sessions                                #
# ------------------------------------------------------------------ #

def test_session_for_a_missing_user_is_cleared(client):
    with client.session_transaction() as sess:
        sess["user_id"] = 999999
        sess["extra"] = "value"

    response = client.get("/")

    assert response.status_code == 200
    assert_signed_out_navbar(response.get_data(as_text=True))
    assert session_of(client) == {}


@pytest.mark.parametrize("value", ["1", "abc", [1], {"id": 1}, 1.0, None, ""])
def test_non_integer_user_id_is_treated_as_signed_out(client, value):
    with client.session_transaction() as sess:
        sess["user_id"] = value

    response = client.get("/")

    assert response.status_code == 200
    assert_signed_out_navbar(response.get_data(as_text=True))
    assert not session_of(client).get("user_id")


def test_non_integer_user_id_never_reaches_the_database(client, monkeypatch):
    import app as app_module

    lookups = []
    monkeypatch.setattr(app_module, "get_user_by_id", lambda user_id: lookups.append(user_id))
    with client.session_transaction() as sess:
        sess["user_id"] = "1 OR 1=1"

    assert client.get("/").status_code == 200
    assert lookups == []


@pytest.mark.parametrize("cookie", ["garbage", "", "eyJ1c2VyX2lkIjoxfQ.AAAAAA.invalid-signature"])
def test_cookie_with_a_bad_signature_is_ignored(client, cookie):
    client.set_cookie("session", cookie)
    response = client.get("/")

    assert response.status_code == 200
    assert_signed_out_navbar(response.get_data(as_text=True))


def test_cookie_signed_with_another_key_is_ignored(app, client):
    from flask import Flask

    other = Flask("other")
    other.secret_key = "some-other-key"
    forged = other.session_interface.get_signing_serializer(other).dumps({"user_id": demo_id()})

    client.set_cookie("session", forged)
    assert_signed_out_navbar(client.get("/").get_data(as_text=True))


def test_user_deleted_while_signed_in_is_signed_out(client):
    other_id = register(client)
    sign_in(client, OTHER["email"], OTHER["password"])
    assert "Sign out" in navbar(client.get("/").get_data(as_text=True))

    c = db.get_db()
    c.execute("DELETE FROM users WHERE id = ?", (other_id,))
    c.commit()
    c.close()

    response = client.get("/")
    assert response.status_code == 200
    assert_signed_out_navbar(response.get_data(as_text=True))
    assert session_of(client) == {}
    # With the session cleared, the sign-in page is reachable again.
    assert client.get("/login").status_code == 200


def test_stale_session_can_sign_in_again(client):
    with client.session_transaction() as sess:
        sess["user_id"] = 999999

    assert sign_in(client).status_code == 302
    assert session_of(client) == {"user_id": demo_id()}


# ------------------------------------------------------------------ #
# Cache header                                                        #
# ------------------------------------------------------------------ #

def test_signed_in_pages_are_not_cached(client):
    sign_in(client)
    assert client.get("/").headers["Cache-Control"] == "no-store"
    assert client.get("/profile").headers["Cache-Control"] == "no-store"
    assert client.get("/logout").headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("path", ["/", "/login", "/register"])
def test_signed_out_pages_have_no_cache_header(client, path):
    assert "no-store" not in client.get(path).headers.get("Cache-Control", "")


def test_pages_after_logout_have_no_cache_header(client):
    sign_in(client)
    client.get("/logout")
    assert "no-store" not in client.get("/").headers.get("Cache-Control", "")


def test_static_files_skip_the_user_lookup(client, monkeypatch):
    import app as app_module

    sign_in(client)
    lookups = []
    real = app_module.get_user_by_id

    def spy(user_id):
        lookups.append(user_id)
        return real(user_id)

    monkeypatch.setattr(app_module, "get_user_by_id", spy)

    response = client.get("/static/css/style.css")
    assert response.status_code == 200
    assert "no-store" not in response.headers.get("Cache-Control", "")
    assert lookups == []
    response.close()

    client.get("/")
    assert lookups == [demo_id()]  # one lookup per page


# ------------------------------------------------------------------ #
# Regression                                                          #
# ------------------------------------------------------------------ #

def test_login_page_markup(client):
    response = client.get("/login")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'action="/login"' in html
    assert html.count('value=""') == 1  # the email starts empty; the password has no value at all
    assert "auth-error" not in html
    assert "auth-success" not in html


def test_get_login_does_not_create_a_session(client):
    client.get("/login")
    assert client.get_cookie("session") is None


def test_signup_then_sign_in_flow(client):
    response = client.post("/register", data=OTHER, follow_redirects=True)
    assert f'<div class="auth-success">{SIGNUP_MESSAGE}</div>' in response.get_data(as_text=True)
    assert "user_id" not in session_of(client)  # registering still does not sign in

    assert sign_in(client, OTHER["email"], OTHER["password"]).status_code == 302
    html = client.get("/").get_data(as_text=True)
    assert "Rahul Mishra" in navbar(html)
    assert SIGNUP_MESSAGE not in html


@pytest.mark.parametrize("signed_in", [False, True])
@pytest.mark.parametrize("path, text", [
    ("/profile", "Profile page — coming in Step 4"),
    ("/expenses/add", "Add expense — coming in Step 7"),
    ("/expenses/1/edit", "Edit expense — coming in Step 8"),
    ("/expenses/1/delete", "Delete expense — coming in Step 9"),
])
def test_placeholder_routes_unchanged(client, signed_in, path, text):
    if signed_in:
        sign_in(client)
    response = client.get(path)
    assert response.status_code == 200
    assert response.get_data(as_text=True) == text


def test_unknown_route_is_still_404_when_signed_in(client):
    sign_in(client)
    assert client.get("/nope").status_code == 404
    assert session_of(client) == {"user_id": demo_id()}


def test_samesite_is_configured(app):
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.permanent_session_lifetime  # default kept; sessions are simply not permanent


def test_nav_user_style_uses_only_css_variables():
    css_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "css", "style.css")
    with open(css_path, encoding="utf-8") as f:
        css = f.read()

    rule = re.search(r"\.nav-user\s*\{([^}]*)\}", css)
    assert rule is not None
    body = rule.group(1)
    assert "#" not in body
    assert "var(--ink-muted)" in body
    assert "text-overflow: ellipsis" in body
    # Hidden on narrow screens, where only the nav-cta button stays.
    assert re.search(r"@media[^{]*\{[^@]*\.nav-user\s*\{\s*display:\s*none;\s*\}", css)
