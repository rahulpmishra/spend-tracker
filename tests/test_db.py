import ast
import os
import re
import sqlite3
import threading
from datetime import date, timedelta

import pytest
from werkzeug.security import check_password_hash

import database.db as db


def count(conn, table):
    # Table names can't be parameterized; only called with the two literal names below.
    return conn.execute({"users": "SELECT COUNT(*) FROM users",
                         "expenses": "SELECT COUNT(*) FROM expenses"}[table]).fetchone()[0]


# ------------------------------------------------------------------ #
# get_db                                                              #
# ------------------------------------------------------------------ #

def test_get_db_creates_file_at_db_path(db_path):
    assert not os.path.exists(db_path)
    db.get_db().close()
    assert os.path.exists(db_path)


def test_get_db_returns_dict_like_rows(conn):
    assert conn.row_factory is sqlite3.Row
    row = conn.execute("SELECT 1 AS one, 'x' AS two").fetchone()
    assert row["one"] == 1 and row["two"] == "x"
    assert dict(row) == {"one": 1, "two": "x"}


def test_get_db_enables_foreign_keys(conn):
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_get_db_foreign_keys_on_every_new_connection(db_path):
    for _ in range(3):
        c = db.get_db()
        assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        c.close()


# ------------------------------------------------------------------ #
# init_db                                                             #
# ------------------------------------------------------------------ #

def test_init_db_creates_both_tables(conn):
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"users", "expenses"} <= names
    assert "sqlite_sequence" in names  # AUTOINCREMENT in use


def test_init_db_is_idempotent(db_path):
    for _ in range(3):
        db.init_db()
    db.seed_db()
    db.init_db()  # must not wipe data
    c = db.get_db()
    assert count(c, "users") == 1 and count(c, "expenses") == 8
    c.close()


def columns(conn, table):
    sql = {"users": "PRAGMA table_info(users)", "expenses": "PRAGMA table_info(expenses)"}[table]
    return {r["name"]: (r["type"], r["notnull"], r["dflt_value"], r["pk"]) for r in conn.execute(sql)}


def test_users_schema(conn):
    assert columns(conn, "users") == {
        "id": ("INTEGER", 0, None, 1),
        "name": ("TEXT", 1, None, 0),
        "email": ("TEXT", 1, None, 0),
        "password_hash": ("TEXT", 1, None, 0),
        "created_at": ("TEXT", 0, "datetime('now')", 0),
    }
    unique = [r for r in conn.execute("PRAGMA index_list(users)") if r["unique"]]
    assert len(unique) == 1
    cols = [r["name"] for r in conn.execute("SELECT name FROM pragma_index_info(?)", (unique[0]["name"],))]
    assert cols == ["email"]


def test_expenses_schema(conn):
    assert columns(conn, "expenses") == {
        "id": ("INTEGER", 0, None, 1),
        "user_id": ("INTEGER", 1, None, 0),
        "amount": ("REAL", 1, None, 0),
        "category": ("TEXT", 1, None, 0),
        "date": ("TEXT", 1, None, 0),
        "description": ("TEXT", 0, None, 0),
        "created_at": ("TEXT", 0, "datetime('now')", 0),
    }
    fks = [dict(r) for r in conn.execute("PRAGMA foreign_key_list(expenses)")]
    assert len(fks) == 1
    assert (fks[0]["table"], fks[0]["from"], fks[0]["to"]) == ("users", "user_id", "id")


def test_categories_constant_matches_check_constraint(conn):
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'expenses'").fetchone()[0]
    in_list = re.search(r"category IN\s*\(([^)]*)\)", sql).group(1)
    assert re.findall(r"'([^']*)'", in_list) == db.CATEGORIES
    assert db.CATEGORIES == ["Food", "Transport", "Bills", "Health", "Entertainment", "Shopping", "Other"]


def test_created_at_defaults_to_now(conn, user_id):
    created = conn.execute("SELECT created_at FROM users WHERE id = ?", (user_id,)).fetchone()[0]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", created)


# ------------------------------------------------------------------ #
# seed_db                                                             #
# ------------------------------------------------------------------ #

@pytest.fixture
def seeded(db_path):
    db.init_db()
    db.seed_db()
    c = db.get_db()
    yield c
    c.close()


def test_seed_creates_demo_user_with_hashed_password(seeded):
    users = seeded.execute("SELECT * FROM users").fetchall()
    assert len(users) == 1
    u = users[0]
    assert (u["name"], u["email"]) == ("Demo User", "demo@spendly.com")
    assert u["password_hash"] != "demo123"
    assert u["password_hash"].startswith("scrypt:")
    assert check_password_hash(u["password_hash"], "demo123")
    assert not check_password_hash(u["password_hash"], "wrong")


def test_seed_creates_8_expenses_for_demo_user(seeded):
    uid = seeded.execute("SELECT id FROM users").fetchone()[0]
    rows = seeded.execute("SELECT * FROM expenses").fetchall()
    assert len(rows) == 8
    assert all(r["user_id"] == uid for r in rows)
    assert {r["category"] for r in rows} == set(db.CATEGORIES)


def test_seed_amounts_are_real_and_positive(seeded):
    rows = seeded.execute("SELECT amount, typeof(amount) AS t FROM expenses").fetchall()
    assert all(r["t"] == "real" and r["amount"] > 0 for r in rows)


def test_seed_dates_are_iso_current_month_not_future(seeded):
    today = date.today()
    for (d,) in seeded.execute("SELECT date FROM expenses"):
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", d)
        parsed = date.fromisoformat(d)
        assert (parsed.year, parsed.month) == (today.year, today.month)
        assert parsed <= today


def test_seed_is_idempotent(db_path):
    db.init_db()
    for _ in range(3):
        db.seed_db()
    c = db.get_db()
    assert count(c, "users") == 1 and count(c, "expenses") == 8
    c.close()


def test_seed_skips_when_any_user_exists(conn, user_id):
    db.seed_db()
    assert count(conn, "users") == 1
    assert count(conn, "expenses") == 0
    assert conn.execute("SELECT 1 FROM users WHERE email = ?", ("demo@spendly.com",)).fetchone() is None


def test_seed_is_atomic_on_failure(db_path, monkeypatch):
    db.init_db()

    def boom(today):
        raise RuntimeError("simulated failure after user insert")

    monkeypatch.setattr(db, "_seed_dates", boom)
    with pytest.raises(RuntimeError):
        db.seed_db()
    c = db.get_db()
    assert count(c, "users") == 0 and count(c, "expenses") == 0
    c.close()

    monkeypatch.undo()
    monkeypatch.setattr(db, "DB_PATH", db_path)
    db.seed_db()  # recovers cleanly on the next run
    c = db.get_db()
    assert count(c, "users") == 1 and count(c, "expenses") == 8
    c.close()


def test_seed_is_safe_under_concurrency(db_path):
    db.init_db()
    workers = 4
    barrier = threading.Barrier(workers)
    errors = []

    def run():
        barrier.wait()
        try:
            db.seed_db()
        except Exception as e:  # pragma: no cover - reported below
            errors.append(e)

    threads = [threading.Thread(target=run) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    c = db.get_db()
    assert count(c, "users") == 1 and count(c, "expenses") == 8
    c.close()


def test_seed_dates_every_day_of_leap_year():
    start = date(2028, 1, 1)
    for offset in range(366):
        today = start + timedelta(days=offset)
        dates = [date.fromisoformat(d) for d in db._seed_dates(today)]
        assert len(dates) == 8
        assert dates == sorted(dates)
        assert dates[0] == today.replace(day=1)
        assert dates[-1] == today
        assert all(d.month == today.month and d.year == today.year and d <= today for d in dates)


# ------------------------------------------------------------------ #
# Constraints required by the spec                                    #
# ------------------------------------------------------------------ #

def test_duplicate_email_rejected(conn, user_id):
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        conn.execute(
            "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
            ("Other", "test@spendly.com", "hash"),
        )


def test_invalid_user_id_rejected(conn):
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        conn.execute(
            "INSERT INTO expenses (user_id, amount, category, date) VALUES (?, ?, ?, ?)",
            (999, 100.0, "Food", "2026-09-01"),
        )


def test_deleting_user_with_expenses_rejected(conn, user_id):
    conn.execute(
        "INSERT INTO expenses (user_id, amount, category, date) VALUES (?, ?, ?, ?)",
        (user_id, 100.0, "Food", "2026-09-01"),
    )
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


@pytest.mark.parametrize("column", ["name", "email", "password_hash"])
def test_users_not_null(conn, column):
    values = {"name": "N", "email": "n@spendly.com", "password_hash": "h"}
    values[column] = None
    with pytest.raises(sqlite3.IntegrityError, match="NOT NULL"):
        conn.execute(
            "INSERT INTO users (name, email, password_hash) VALUES (:name, :email, :password_hash)",
            values,
        )


@pytest.mark.parametrize("column", ["user_id", "amount", "category", "date"])
def test_expenses_not_null(conn, user_id, column):
    values = {"user_id": user_id, "amount": 100.0, "category": "Food", "date": "2026-09-01"}
    values[column] = None
    with pytest.raises(sqlite3.IntegrityError, match="NOT NULL"):
        conn.execute(
            "INSERT INTO expenses (user_id, amount, category, date) "
            "VALUES (:user_id, :amount, :category, :date)",
            values,
        )


def test_description_is_nullable(conn, user_id):
    conn.execute(
        "INSERT INTO expenses (user_id, amount, category, date, description) VALUES (?, ?, ?, ?, ?)",
        (user_id, 100.0, "Food", "2026-09-01", None),
    )


def test_invalid_sql_raises_clear_error(conn):
    with pytest.raises(sqlite3.OperationalError, match="syntax error"):
        conn.execute("SELEC * FROM users")
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        conn.execute("SELECT * FROM nope")


# ------------------------------------------------------------------ #
# Hardening constraints (beyond spec, agreed during planning)         #
# ------------------------------------------------------------------ #

def insert_expense(conn, user_id, amount=100.0, category="Food", when="2026-09-01"):
    conn.execute(
        "INSERT INTO expenses (user_id, amount, category, date) VALUES (?, ?, ?, ?)",
        (user_id, amount, category, when),
    )
    return conn.execute(
        "SELECT amount, typeof(amount) AS t, category, date FROM expenses ORDER BY id DESC LIMIT 1"
    ).fetchone()


@pytest.mark.parametrize("email", ["TEST@spendly.com", "Test@Spendly.COM"])
def test_email_unique_case_insensitive(conn, user_id, email):
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        conn.execute(
            "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)", ("X", email, "h")
        )


def test_email_lookup_case_insensitive(conn, user_id):
    row = conn.execute("SELECT id FROM users WHERE email = ?", ("TEST@SPENDLY.COM",)).fetchone()
    assert row["id"] == user_id


@pytest.mark.parametrize("amount", [0, 0.0, -1, -250.5, "abc", "", b"\x01"])
def test_bad_amount_rejected(conn, user_id, amount):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        insert_expense(conn, user_id, amount=amount)


@pytest.mark.parametrize("amount, stored", [(500, 500.0), (499.5, 499.5), ("12.5", 12.5), (0.01, 0.01)])
def test_good_amount_stored_as_real(conn, user_id, amount, stored):
    row = insert_expense(conn, user_id, amount=amount)
    assert row["amount"] == stored and row["t"] == "real"


@pytest.mark.parametrize("category", ["food", "FOOD", "Gadgets", "", " Food", "Food "])
def test_bad_category_rejected(conn, user_id, category):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        insert_expense(conn, user_id, category=category)


@pytest.mark.parametrize("category", db.CATEGORIES)
def test_every_category_accepted(conn, user_id, category):
    assert insert_expense(conn, user_id, category=category)["category"] == category


@pytest.mark.parametrize("when", [
    "2026-02-29", "2026-02-30", "2026-04-31", "2026-13-01", "2026-00-10", "2026-9-7",
    "27/09/2026", "09/27/2026", "", " 2026-09-27", "2026-09-27 ", "2026-09-27 10:00:00",
    "2026-09-27T10:00", "20260927", 20260927, "2461310.5", "today", "now", "localtime",
    "１２３４-０９-２７",
])
def test_bad_date_rejected(conn, user_id, when):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        insert_expense(conn, user_id, when=when)


@pytest.mark.parametrize("when", ["2026-09-27", "2028-02-29", "2026-12-31", "2026-01-01"])
def test_good_date_accepted(conn, user_id, when):
    assert insert_expense(conn, user_id, when=when)["date"] == when


# ------------------------------------------------------------------ #
# Parameterized SQL only                                              #
# ------------------------------------------------------------------ #

def test_db_module_never_formats_sql():
    tree = ast.parse(open(db.__file__, encoding="utf-8").read())
    for node in ast.walk(tree):
        assert not isinstance(node, ast.JoinedStr), f"f-string at line {node.lineno}"
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
            raise AssertionError(f"% formatting at line {node.lineno}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr != "format", f".format() at line {node.lineno}"
            if node.func.attr in ("execute", "executemany", "executescript"):
                sql = node.args[0]
                ok = isinstance(sql, ast.Constant) or (isinstance(sql, ast.Name) and sql.id == "SCHEMA")
                assert ok, f"non-literal SQL at line {node.lineno}"
