"""Tests for headroom.code_tools.sql -- read-only SQL through a connection name."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from headroom.code_tools import sql


def _resolver_that_must_not_be_called(name: str) -> str:
    raise AssertionError("resolver must not be called for a refused statement")


@pytest.fixture
def people_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "people.db"
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE people (id INTEGER, name TEXT)")
    connection.executemany(
        "INSERT INTO people (id, name) VALUES (?, ?)",
        [(1, "Alice"), (2, "Bob"), (3, "Carol")],
    )
    connection.commit()
    connection.close()
    return db_path


@pytest.fixture
def five_row_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "five_rows.db"
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE items (id INTEGER)")
    connection.executemany(
        "INSERT INTO items (id) VALUES (?)",
        [(1,), (2,), (3,), (4,), (5,)],
    )
    connection.commit()
    connection.close()
    return db_path


def _sqlite_resolver(db_path: Path):
    def resolver(name: str) -> str:
        assert name == "mydb"
        return f"sqlite://{db_path}"

    return resolver


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE accounts SET balance = 0",
        "DELETE FROM accounts",
        "INSERT INTO accounts (id) VALUES (1)",
        "DROP TABLE accounts",
        "SELECT * FROM accounts; DROP TABLE accounts",
    ],
)
def test_query_refuses_non_read_only_statements_before_touching_driver(statement: str) -> None:
    result = sql.query(
        {"connection": "mydb", "sql": statement, "limit": 200},
        _resolver_that_must_not_be_called,
    )

    assert result.startswith("Refused:")


def test_query_select_returns_expected_rows_as_a_table(people_db: Path) -> None:
    result = sql.query(
        {"connection": "mydb", "sql": "SELECT id, name FROM people ORDER BY id"},
        _sqlite_resolver(people_db),
    )

    assert result == ("id | name \n---+------\n1  | Alice\n2  | Bob  \n3  | Carol\n3 rows")


def test_query_row_cap_returns_capped_footer(five_row_db: Path) -> None:
    result = sql.query(
        {"connection": "mydb", "sql": "SELECT id FROM items ORDER BY id", "limit": 2},
        _sqlite_resolver(five_row_db),
    )

    assert result == ("id\n--\n1 \n2 \n2 rows (capped)")


def test_query_schema_lists_tables_and_columns(people_db: Path) -> None:
    result = sql.query(
        {"connection": "mydb", "action": "schema"},
        _sqlite_resolver(people_db),
    )

    assert result == ("people:\n  id INTEGER\n  name TEXT")


def test_open_sqlite_readonly_refuses_writes_even_if_the_guard_is_bypassed(
    people_db: Path,
) -> None:
    connection = sql.open_sqlite_readonly(str(people_db))
    try:
        with pytest.raises(sqlite3.OperationalError) as exc_info:
            connection.execute("INSERT INTO people (id, name) VALUES (4, 'Dave')")
    finally:
        connection.close()

    assert "readonly" in str(exc_info.value).lower()


class _FakePostgresCursor:
    def __init__(self, executed: list[str]) -> None:
        self._executed = executed
        self.description = [("id",), ("name",)]

    def execute(self, statement: str) -> None:
        self._executed.append(statement)

    def fetchmany(self, size: int) -> list[tuple[int, str]]:
        return [(1, "Alice")]


class _FakePostgresConnection:
    def __init__(self, executed: list[str]) -> None:
        self._executed = executed
        self.closed = False

    def cursor(self) -> _FakePostgresCursor:
        return _FakePostgresCursor(self._executed)

    def close(self) -> None:
        self.closed = True


def test_postgres_query_issues_begin_read_only_before_the_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed: list[str] = []

    def fake_connect(url: str) -> _FakePostgresConnection:
        assert url == "postgresql://alice:secret@localhost:5432/app"
        return _FakePostgresConnection(executed)

    monkeypatch.setattr(sql, "postgres_connect", fake_connect)

    def resolver(name: str) -> str:
        return "postgresql://alice:secret@localhost:5432/app"

    result = sql.query(
        {"connection": "mydb", "sql": "SELECT id, name FROM people"},
        resolver,
    )

    assert executed == ["BEGIN READ ONLY", "SELECT id, name FROM people"]
    assert result == ("id | name \n---+------\n1  | Alice\n1 row")
