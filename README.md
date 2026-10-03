# Spendly

A personal expense tracker built with Flask and SQLite. All amounts are in Indian rupees (₹).

The app is being built step by step. Pages and routes that aren't implemented yet return a placeholder such as `Add expense — coming in Step 7`.

## Status

| Step | Feature | Status |
|---|---|---|
| 01 | Database setup (SQLite schema, demo data) | Done |
| 02 | Registration | Planned |
| 03 | Login / logout | Planned |
| 04 | Profile | Planned |
| 07–09 | Add / edit / delete expenses | Planned |

## Getting started

Developed with Python 3.13.

```bash
python -m venv venv
venv\Scripts\activate            # Windows  (macOS/Linux: source venv/bin/activate)
pip install -r requirements.txt
python app.py                    # http://127.0.0.1:5001
```

On first start the app creates `expense_tracker.db` in the project root and adds demo data:

- **Demo login:** `demo@spendly.com` / `demo123`
- **8 sample expenses** covering every category, dated between the 1st of the current month and today

Restarting never duplicates the demo data. To start fresh, stop the app and delete `expense_tracker.db`.

## Running tests

```bash
python -m pytest
```

Tests use a temporary database for each test and never touch `expense_tracker.db`.

## Database

SQLite with no ORM, parameterized queries only, and foreign keys enforced on every connection. It lives in `database/db.py`:

| Function | Purpose |
|---|---|
| `get_db()` | Opens a connection with dict-style rows (`sqlite3.Row`) and `PRAGMA foreign_keys = ON`. The caller closes it. |
| `init_db()` | Creates the tables if they don't exist. Safe to run repeatedly. |
| `seed_db()` | Inserts the demo user and sample expenses only when the `users` table is empty. Atomic and safe if several processes start at once. |

**`users`**

| Column | Type | Constraints |
|---|---|---|
| id | INTEGER | Primary key, autoincrement |
| name | TEXT | Not null |
| email | TEXT | Not null, unique (case-insensitive) |
| password_hash | TEXT | Not null (werkzeug scrypt hash) |
| created_at | TEXT | Default `datetime('now')` |

**`expenses`**

| Column | Type | Constraints |
|---|---|---|
| id | INTEGER | Primary key, autoincrement |
| user_id | INTEGER | Not null, foreign key → `users.id` |
| amount | REAL | Not null, must be a number > 0 |
| category | TEXT | Not null, one of: Food, Transport, Bills, Health, Entertainment, Shopping, Other |
| date | TEXT | Not null, a valid `YYYY-MM-DD` date |
| description | TEXT | Optional |
| created_at | TEXT | Default `datetime('now')` |

Invalid data raises `sqlite3.IntegrityError`. That covers a duplicate email, an unknown user, a bad amount, category or date, and deleting a user who still has expenses.

## Project structure

```
app.py               Flask app and routes; initialises and seeds the DB on startup
database/db.py       SQLite connection, schema and seed data
templates/           Jinja templates (base.html layout, landing, login, register)
static/css/style.css Stylesheet with design tokens as CSS custom properties
static/js/main.js    Client-side script (placeholder)
tests/               pytest suite (database and route tests)
```
