import os
import sqlite3

from flask import Flask, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from database.db import create_user, get_db, get_user_by_email, get_user_by_id, init_db, seed_db

app = Flask(__name__)

# Development fallback only. Set SECRET_KEY in the environment for anything real.
app.secret_key = os.environ.get("SECRET_KEY") or "spendly-dev-secret-key"
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

NAME_MAX = 100
EMAIL_MAX = 254
PASSWORD_MIN = 6
PASSWORD_MAX = 128

LOGIN_FAILED = "Invalid email or password."

# Checked when the email is unknown, so that case costs the same as a wrong password.
_DUMMY_HASH = generate_password_hash("spendly-dummy-password")

# Make sure the schema and demo data exist before any route is served.
with app.app_context():
    init_db()
    seed_db()


@app.before_request
def load_current_user():
    """Put the signed-in user's row on g.user (None when signed out) for routes and templates."""
    g.user = None
    if request.endpoint == "static":
        return
    user_id = session.get("user_id")
    if user_id is None:
        return
    if isinstance(user_id, int):
        g.user = get_user_by_id(user_id)
    if g.user is None:
        session.clear()  # the account is gone, or the value is not an id


@app.after_request
def no_store_when_signed_in(response):
    # Keeps the back button from showing a signed-in page after sign-out.
    if g.get("user") is not None:
        response.headers["Cache-Control"] = "no-store"
    return response


# ------------------------------------------------------------------ #
# Routes                                                              #
# ------------------------------------------------------------------ #

@app.route("/")
def landing():
    return render_template("landing.html")


def _is_valid_email(email):
    """Loose shape check: one @, something before it, and a dotted domain with no empty labels."""
    if any(ch.isspace() for ch in email) or email.count("@") != 1:
        return False
    local, domain = email.split("@")
    return bool(local) and "." in domain and all(domain.split("."))


def _registration_error(name, email, password):
    """Return the first validation problem as a message, or None if the input is acceptable."""
    if not name:
        return "Please enter your name."
    if len(name) > NAME_MAX:
        return f"Name must be {NAME_MAX} characters or fewer."
    if not email:
        return "Please enter your email address."
    if len(email) > EMAIL_MAX:
        return f"Email must be {EMAIL_MAX} characters or fewer."
    if not _is_valid_email(email):
        return "Please enter a valid email address."
    if len(password) < PASSWORD_MIN:
        return f"Password must be at least {PASSWORD_MIN} characters."
    if len(password) > PASSWORD_MAX:
        return f"Password must be {PASSWORD_MAX} characters or fewer."
    return None


@app.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("landing"))

    if request.method == "GET":
        return render_template("register.html")

    # A missing field counts as empty rather than raising.
    raw_name = request.form.get("name", "")
    raw_email = request.form.get("email", "")
    password = request.form.get("password", "")  # never trimmed, re-rendered or logged

    name = raw_name.strip()
    email = raw_email.strip().lower()

    def form_with_error(message, status):
        return render_template("register.html", error=message, name=raw_name, email=raw_email), status

    error = _registration_error(name, email, password)
    if error:
        return form_with_error(error, 400)

    try:
        create_user(name, email, generate_password_hash(password))
    except sqlite3.IntegrityError:
        # The UNIQUE constraint is the duplicate check, so simultaneous sign-ups can't both succeed.
        return form_with_error("An account with this email already exists.", 409)

    flash("Account created. Please sign in.", "success")
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("landing"))

    if request.method == "GET":
        return render_template("login.html")

    # A missing field counts as empty rather than raising.
    raw_email = request.form.get("email", "")
    password = request.form.get("password", "")  # never trimmed, re-rendered or logged

    email = raw_email.strip().lower()

    def form_with_error(message, status):
        return render_template("login.html", error=message, email=raw_email), status

    if not email:
        return form_with_error("Please enter your email address.", 400)
    if not password:
        return form_with_error("Please enter your password.", 400)
    # No account can have values this long, so skip the lookup and the hash.
    if len(email) > EMAIL_MAX or len(password) > PASSWORD_MAX:
        return form_with_error(LOGIN_FAILED, 401)

    user = get_user_by_email(email)
    password_ok = check_password_hash(user["password_hash"] if user else _DUMMY_HASH, password)
    if user is None or not password_ok:
        # Same message for both, so the form doesn't reveal which emails are registered.
        return form_with_error(LOGIN_FAILED, 401)

    session.clear()  # nothing from an earlier session carries over
    session["user_id"] = user["id"]
    return redirect(url_for("landing"))


@app.route("/logout")
def logout():
    was_signed_in = g.user is not None
    session.clear()
    if was_signed_in:
        flash("You have been signed out.", "success")
    return redirect(url_for("login"))


# ------------------------------------------------------------------ #
# Placeholder routes — we will implement these                  #
# ------------------------------------------------------------------ #

@app.route("/profile")
def profile():
    return "Profile page — coming in Step 4"


@app.route("/expenses/add")
def add_expense():
    return "Add expense — coming in Step 7"


@app.route("/expenses/<int:id>/edit")
def edit_expense(id):
    return "Edit expense — coming in Step 8"


@app.route("/expenses/<int:id>/delete")
def delete_expense(id):
    return "Delete expense — coming in Step 9"


if __name__ == "__main__":
    app.run(debug=True, port=5001)
