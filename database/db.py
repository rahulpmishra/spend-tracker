import os
import sqlite3
from datetime import date

from werkzeug.security import generate_password_hash

# Database file lives in the project root, regardless of the working directory.
DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "expense_tracker.db",
)

# Fixed category list. Keep in sync with the CHECK constraint in init_db().
CATEGORIES = ["Food", "Transport", "Bills", "Health", "Entertainment", "Shopping", "Other"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    created_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS expenses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    amount      REAL NOT NULL CHECK (typeof(amount) = 'real' AND amount > 0),
    category    TEXT NOT NULL CHECK (category IN
                    ('Food', 'Transport', 'Bills', 'Health', 'Entertainment', 'Shopping', 'Other')),
    -- Shape check first: date() on values like 'now' raises instead of failing the CHECK.
    date        TEXT NOT NULL CHECK (CASE
                    WHEN date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
                    THEN date IS date(date)
                    ELSE 0
                END),
    description TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);
"""

# (category, amount in INR, description) for the demo user's sample expenses.
SAMPLE_EXPENSES = [
    ("Food", 850.00, "Groceries from kirana store"),
    ("Transport", 500.00, "Metro card recharge"),
    ("Bills", 1850.00, "Electricity bill"),
    ("Health", 420.00, "Pharmacy medicines"),
    ("Entertainment", 600.00, "Movie tickets"),
    ("Shopping", 1499.00, "Kurta from mall"),
    ("Other", 750.00, "Birthday gift for friend"),
    ("Food", 1200.00, "Dinner with friends"),
]


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Must run outside a transaction, otherwise SQLite silently ignores it.
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _seed_dates(today, n=len(SAMPLE_EXPENSES)):
    """Spread n dates evenly from the 1st of today's month up to today (never in the future)."""
    return [
        date(today.year, today.month, 1 + round(i * (today.day - 1) / (n - 1))).isoformat()
        for i in range(n)
    ]


def seed_db():
    conn = get_db()
    try:
        # Take the write lock before checking, so two processes starting at once can't both seed.
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None:
            conn.rollback()
            return

        cursor = conn.execute(
            "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
            ("Demo User", "demo@spendly.com", generate_password_hash("demo123")),
        )
        user_id = cursor.lastrowid

        dates = _seed_dates(date.today())
        conn.executemany(
            "INSERT INTO expenses (user_id, amount, category, date, description) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (user_id, amount, category, expense_date, description)
                for (category, amount, description), expense_date in zip(SAMPLE_EXPENSES, dates)
            ],
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
