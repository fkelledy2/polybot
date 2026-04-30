# db.py
# ─────────────────────────────────────────────────────────────
# Database abstraction layer.
# Routes to SQLite (default) or Postgres (when DATABASE_URL is set).
# All other modules use db.get_connection() instead of sqlite3 directly.
# ─────────────────────────────────────────────────────────────

import os
import re
import sqlite3

DATABASE_URL = os.getenv("DATABASE_URL", "")
IS_POSTGRES  = bool(DATABASE_URL)
placeholder  = "%s" if IS_POSTGRES else "?"
_TRADES_DB   = os.getenv("TRADES_DB", "trades.db")

if IS_POSTGRES:
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError:
        IS_POSTGRES = False
        placeholder  = "?"


def get_connection():
    """Return a database connection. SQLite connections have row_factory set."""
    if IS_POSTGRES:
        return psycopg2.connect(DATABASE_URL)
    conn = sqlite3.connect(_TRADES_DB, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def get_cursor(conn):
    """Return a cursor. Postgres uses RealDictCursor for dict-like row access."""
    if IS_POSTGRES:
        return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    return conn.cursor()


def adapt_schema(sql: str) -> str:
    """Convert SQLite DDL to Postgres DDL when IS_POSTGRES is True."""
    if IS_POSTGRES:
        sql = re.sub(
            r'\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b',
            'SERIAL PRIMARY KEY',
            sql, flags=re.IGNORECASE,
        )
    return sql


def safe_alter(conn, sql: str) -> None:
    """Run ALTER TABLE, ignoring duplicate-column errors (idempotent migration)."""
    c = get_cursor(conn)
    try:
        c.execute(sql)
        conn.commit()
    except Exception:
        conn.rollback()
