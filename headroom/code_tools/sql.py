"""Read-only SQL access through a named connection reference.

The code agent never gets a raw connection string. It passes a connection
name and a SQL statement; a resolver (bound to a connection reference store
and a keychain) turns the name into a URL. Only a single read statement is
allowed -- no writes, no multiple statements -- and that is checked twice:
once by reading the SQL text before any driver is touched, and again by
opening the database itself in a mode that refuses writes.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from headroom.code_tools.connections import kind_from_url

_DEFAULT_LIMIT = 200
_MAX_LIMIT = 1000

_READ_ONLY_PATTERN = re.compile(r"^\s*(SELECT|WITH|EXPLAIN|SHOW|PRAGMA)\b", re.IGNORECASE)


def query(request: dict[str, Any], resolver: Callable[[str], str]) -> str:
    """Run a read-only query (or fetch schema) for a named connection.

    ``request`` is either ``{"connection": name, "sql": "...", "limit": n}``
    or ``{"connection": name, "action": "schema"}``. Returns a plain-text
    result: a compact table, a schema listing, or a one-line refusal.
    """

    connection_name = request.get("connection")
    if not connection_name or not isinstance(connection_name, str):
        return 'Refused: request is missing a "connection" name.'

    action = request.get("action")
    if action is not None:
        if action != "schema":
            return f'Refused: unknown action {action!r}. Only "schema" is supported.'
        return _run_schema(connection_name, resolver)

    raw_sql = request.get("sql")
    if not raw_sql or not isinstance(raw_sql, str):
        return 'Refused: request is missing a "sql" statement.'

    refusal = _refuse_unless_read_only(raw_sql)
    if refusal is not None:
        return refusal

    limit = _clamp_limit(request.get("limit"))
    url = resolver(connection_name)
    connection = _open(url)
    try:
        result = _select(connection, raw_sql, limit)
    finally:
        connection.close()
    return _render_table(result.columns, result.rows, result.truncated)


def _scan_single_quoted(text: str, start: int) -> int:
    """Return the index just past the closing quote of a `'...'` literal
    that starts at `start`. `''` inside the literal is an escaped quote, not
    the end -- so a plain "find the next quote" would stop one character
    too early. An unterminated literal scans to the end of the text."""

    length = len(text)
    i = start + 1
    while i < length:
        if text[i] == "'":
            if i + 1 < length and text[i + 1] == "'":
                i += 2
                continue
            return i + 1
        i += 1
    return length


def _scan_double_quoted(text: str, start: int) -> int:
    """Return the index just past the closing quote of a `"..."` identifier
    that starts at `start`. An unterminated one scans to the end."""

    length = len(text)
    i = start + 1
    while i < length:
        if text[i] == '"':
            return i + 1
        i += 1
    return length


def _split_statements(text: str) -> list[str]:
    """Split `text` into SQL statements at top-level semicolons.

    A `;` inside a single-quoted string, a double-quoted identifier, a `--`
    line comment, or a `/* ... */` block comment does not end a statement --
    only a `;` outside all of those does. This is a small hand-rolled
    scanner, not a full SQL parser, but it covers the punctuation that
    actually changes where one statement ends and the next begins.
    """

    length = len(text)
    statements: list[str] = []
    piece_start = 0
    i = 0
    while i < length:
        ch = text[i]
        if ch == "'":
            i = _scan_single_quoted(text, i)
            continue
        if ch == '"':
            i = _scan_double_quoted(text, i)
            continue
        if text.startswith("--", i):
            newline = text.find("\n", i)
            i = length if newline == -1 else newline
            continue
        if text.startswith("/*", i):
            close = text.find("*/", i + 2)
            i = length if close == -1 else close + 2
            continue
        if ch == ";":
            statements.append(text[piece_start:i])
            i += 1
            piece_start = i
            continue
        i += 1
    statements.append(text[piece_start:length])
    return statements


def _refuse_unless_read_only(raw_sql: str) -> str | None:
    stripped = raw_sql.strip()
    if not stripped:
        return "Refused: empty SQL statement."
    statements = [part.strip() for part in _split_statements(stripped) if part.strip()]
    if len(statements) != 1:
        return "Refused: only a single statement is allowed, not multiple statements."
    statement = statements[0]
    if not _READ_ONLY_PATTERN.match(statement):
        return "Refused: only SELECT, WITH, EXPLAIN, SHOW, or PRAGMA statements are allowed."
    return None


def _clamp_limit(raw_limit: Any) -> int:
    if raw_limit is None:
        return _DEFAULT_LIMIT
    try:
        value = int(raw_limit)
    except (TypeError, ValueError):
        return _DEFAULT_LIMIT
    if value <= 0:
        return _DEFAULT_LIMIT
    return min(value, _MAX_LIMIT)


@dataclass(frozen=True)
class QueryResult:
    """The result of a single read-only ``SELECT``."""

    columns: list[str]
    rows: list[tuple[Any, ...]]
    truncated: bool


# ---------------------------------------------------------------------------
# Connecting -- one dispatcher, driver-specific only in how it opens
# ---------------------------------------------------------------------------


def _sqlite_path_from_url(url: str) -> str:
    return urlparse(url).path


def open_sqlite_readonly(path: str) -> sqlite3.Connection:
    """Open a SQLite file so that any write against it raises an error.

    Used both by the query path and directly by tests that prove the
    read-only guard holds at the driver level, not just in the SQL text
    check.
    """

    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


# No Postgres driver (psycopg, psycopg2, asyncpg) is installed in this
# project's venv, and the brief for this slice says not to add one. So the
# connect function is a module-level hook: at runtime it lazily imports
# whichever driver is available, and tests replace it with a fake to prove
# the read-only transaction wrapping without a real Postgres server.


def _default_postgres_connect(url: str) -> Any:
    try:
        import psycopg  # type: ignore[import-not-found]

        return psycopg.connect(url)
    except ModuleNotFoundError:
        pass
    try:
        import psycopg2  # type: ignore[import-not-found]

        return psycopg2.connect(url)
    except ModuleNotFoundError:
        pass
    raise ModuleNotFoundError(
        "no Postgres driver is installed (tried psycopg, psycopg2); "
        "install one to query Postgres connections"
    )


postgres_connect: Callable[[str], Any] = _default_postgres_connect


def _open(url: str) -> Any:
    """Open ``url`` ready for read-only statements: sqlite in ``mode=ro``,
    Postgres with a ``BEGIN READ ONLY`` transaction already started."""

    if kind_from_url(url) == "sqlite":
        return open_sqlite_readonly(_sqlite_path_from_url(url))
    connection = postgres_connect(url)
    connection.cursor().execute("BEGIN READ ONLY")
    return connection


# ---------------------------------------------------------------------------
# Select -- identical cursor/fetch/truncate logic for either driver
# ---------------------------------------------------------------------------


def _select(connection: Any, raw_sql: str, limit: int) -> QueryResult:
    cursor = connection.cursor()
    cursor.execute(raw_sql)
    columns = [description[0] for description in cursor.description or []]
    fetched = cursor.fetchmany(limit + 1)
    truncated = len(fetched) > limit
    rows = [tuple(row) for row in fetched[:limit]]
    return QueryResult(columns=columns, rows=rows, truncated=truncated)


# ---------------------------------------------------------------------------
# Schema -- the queries are backend-specific, but connecting/closing is not
# ---------------------------------------------------------------------------


def _schema(connection: Any) -> list[tuple[str, list[tuple[str, str]]]]:
    if isinstance(connection, sqlite3.Connection):
        return _sqlite_schema_tables(connection)
    return _postgres_schema_tables(connection)


def _sqlite_schema_tables(
    connection: sqlite3.Connection,
) -> list[tuple[str, list[tuple[str, str]]]]:
    cursor = connection.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
    table_names = [row[0] for row in cursor.fetchall()]
    tables = []
    for table_name in table_names:
        cursor.execute(f'PRAGMA table_info("{table_name}")')
        columns = [(row[1], row[2]) for row in cursor.fetchall()]
        tables.append((table_name, columns))
    return tables


def _postgres_schema_tables(connection: Any) -> list[tuple[str, list[tuple[str, str]]]]:
    cursor = connection.cursor()
    cursor.execute(
        "SELECT table_name, column_name, data_type "
        "FROM information_schema.columns "
        "WHERE table_schema = 'public' "
        "ORDER BY table_name, ordinal_position"
    )
    tables: list[tuple[str, list[tuple[str, str]]]] = []
    by_table: dict[str, list[tuple[str, str]]] = {}
    for table_name, column_name, data_type in cursor.fetchall():
        if table_name not in by_table:
            by_table[table_name] = []
            tables.append((table_name, by_table[table_name]))
        by_table[table_name].append((column_name, data_type))
    return tables


def _run_schema(connection_name: str, resolver: Callable[[str], str]) -> str:
    url = resolver(connection_name)
    connection = _open(url)
    try:
        tables = _schema(connection)
    finally:
        connection.close()
    return _render_schema(tables)


def _render_schema(tables: Sequence[tuple[str, list[tuple[str, str]]]]) -> str:
    if not tables:
        return "No tables."
    lines: list[str] = []
    for table_name, columns in tables:
        lines.append(f"{table_name}:")
        for column_name, column_type in columns:
            lines.append(f"  {column_name} {column_type}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Table rendering
# ---------------------------------------------------------------------------


def _render_table(columns: Sequence[str], rows: Sequence[tuple[Any, ...]], truncated: bool) -> str:
    str_rows = [[_cell_to_str(value) for value in row] for row in rows]
    widths = [len(column) for column in columns]
    for str_row in str_rows:
        for index, cell in enumerate(str_row):
            widths[index] = max(widths[index], len(cell))

    lines = [_format_row(list(columns), widths), _format_separator(widths)]
    lines.extend(_format_row(str_row, widths) for str_row in str_rows)

    count = len(rows)
    if truncated:
        lines.append(f"{count} rows (capped)")
    else:
        noun = "row" if count == 1 else "rows"
        lines.append(f"{count} {noun}")
    return "\n".join(lines)


def _format_row(cells: list[str], widths: list[int]) -> str:
    return " | ".join(cell.ljust(width) for cell, width in zip(cells, widths))


def _format_separator(widths: list[int]) -> str:
    return "-+-".join("-" * width for width in widths)


def _cell_to_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


__all__ = ["query", "open_sqlite_readonly", "postgres_connect"]
