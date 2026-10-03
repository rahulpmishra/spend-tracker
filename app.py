import os
import sqlite3

from flask import Flask, flash, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

from database.db import create_user, get_db, init_db, seed_db

app = Flask(__name__)

# Development fallback only. Set SECRET_KEY in the environment for anything real.
app.secret_key = os.environ.get("SECRET_KEY") or "spendly-dev-secret-key"

NAME_MAX = 100
EMAIL_MAX = 254
PASSWORD_MIN = 6
PASSWORD_MAX = 128

# Make sure the schema and demo data exist before any route is served.
with app.app_context():
    init_db()
    seed_db()


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


@app.route("/login")
def login():
    return render_template("login.html")


# ------------------------------------------------------------------ #
# Placeholder routes — we will implement these                  #
# ------------------------------------------------------------------ #

@app.route("/logout")
def logout():
    return "Logout — coming in Step 3"


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
