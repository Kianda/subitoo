"""SQLite persistence: connection, versioned-SQL migrations, and data access.

Raw sqlite3 (no ORM) with WAL. Connections are short-lived per CLI invocation, which
suits the exec-per-heartbeat model. Schema is core-owned (see migrations/).
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from subitoo.config import get_settings
from subitoo.core.models import Filters, Query, QueryStatus

# Migrations live inside the package so they ship in the wheel and resolve no matter
# where the code is installed (editable /app or site-packages).
MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def now() -> int:
    return int(time.time())


def connect(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or get_settings().db_path
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)  # autocommit; we manage txns
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


# --- migrations -----------------------------------------------------------------

def _migration_files() -> list[tuple[int, Path]]:
    files = []
    for p in sorted(MIGRATIONS_DIR.glob("*.sql")):
        num = int(p.name.split("_", 1)[0])
        files.append((num, p))
    return files


def migrate(conn: sqlite3.Connection) -> int:
    """Apply any migration files newer than the DB's user_version. Returns new version."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    applied = current
    for num, path in _migration_files():
        if num <= current:
            continue
        # executescript manages its own transaction (it commits any pending one),
        # so we run the DDL then stamp the version. DDL statements are individually
        # durable; a failed migration simply leaves user_version un-bumped.
        conn.executescript(path.read_text())
        conn.execute(f"PRAGMA user_version={num}")
        applied = num
    return applied


def init_db(db_path: str | None = None) -> sqlite3.Connection:
    conn = connect(db_path)
    migrate(conn)
    return conn


# --- row -> model ----------------------------------------------------------------

def _row_to_query(r: sqlite3.Row) -> Query:
    return Query(
        id=r["id"],
        name=r["name"],
        site=r["site"],
        search=json.loads(r["search_json"]),
        filters=Filters.model_validate_json(r["filters_json"]),
        cron=r["cron"],
        enabled=bool(r["enabled"]),
        seeded=bool(r["seeded"]),
        run_delay_seconds=r["run_delay_seconds"],
        status=QueryStatus(r["status"]),
        status_changed_at=r["status_changed_at"],
        last_run_at=r["last_run_at"],
        next_run_at=r["next_run_at"],
        created_at=r["created_at"],
        updated_at=r["updated_at"],
    )


# --- queries CRUD ----------------------------------------------------------------

def create_query(conn, *, name: str, site: str, search: dict, filters: Filters,
                 cron: str, enabled: bool = True, run_delay_seconds: int = 5) -> int:
    ts = now()
    cur = conn.execute(
        """INSERT INTO queries
           (name, site, search_json, filters_json, cron, enabled, seeded,
            run_delay_seconds, status, status_changed_at, created_at, updated_at)
           VALUES (?,?,?,?,?,?,0,?,'pending',?,?,?)""",
        (name, site, json.dumps(search), filters.model_dump_json(), cron,
         int(enabled), int(run_delay_seconds), ts, ts, ts),
    )
    return cur.lastrowid


def get_query(conn, query_id: int) -> Query | None:
    r = conn.execute("SELECT * FROM queries WHERE id=?", (query_id,)).fetchone()
    return _row_to_query(r) if r else None


def get_query_by_name(conn, name: str) -> Query | None:
    r = conn.execute("SELECT * FROM queries WHERE name=?", (name,)).fetchone()
    return _row_to_query(r) if r else None


def list_queries(conn) -> list[Query]:
    rows = conn.execute("SELECT * FROM queries ORDER BY id").fetchall()
    return [_row_to_query(r) for r in rows]


def update_query(conn, query_id: int, **fields) -> None:
    if not fields:
        return
    # serialize known complex fields
    if "search" in fields:
        fields["search_json"] = json.dumps(fields.pop("search"))
    if "filters" in fields:
        fields["filters_json"] = fields.pop("filters").model_dump_json()
    if "enabled" in fields:
        fields["enabled"] = int(fields["enabled"])
    if "seeded" in fields:
        fields["seeded"] = int(fields["seeded"])
    fields["updated_at"] = now()
    cols = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE queries SET {cols} WHERE id=?", (*fields.values(), query_id))


def delete_query(conn, query_id: int) -> None:
    conn.execute("DELETE FROM queries WHERE id=?", (query_id,))


def set_status(conn, query_id: int, status: QueryStatus, ts: int | None = None) -> None:
    conn.execute(
        "UPDATE queries SET status=?, status_changed_at=? WHERE id=?",
        (status.value, ts or now(), query_id),
    )


# --- dispatcher helpers ----------------------------------------------------------

def enabled_pending_queries(conn) -> list[Query]:
    rows = conn.execute(
        "SELECT * FROM queries WHERE enabled=1 AND status='pending'"
    ).fetchall()
    return [_row_to_query(r) for r in rows]


def claim_query(conn, query_id: int, ts: int | None = None) -> bool:
    """Atomically move pending -> running. Returns True iff we won the claim."""
    ts = ts or now()
    cur = conn.execute(
        "UPDATE queries SET status='running', status_changed_at=? "
        "WHERE id=? AND status='pending'",
        (ts, query_id),
    )
    return cur.rowcount == 1


def reclaim_stale(conn, timeout: int, ts: int | None = None) -> list[int]:
    """Move queries stuck in 'running' past the timeout -> 'error'. Returns their ids."""
    ts = ts or now()
    cutoff = ts - timeout
    rows = conn.execute(
        "SELECT id FROM queries WHERE status='running' AND status_changed_at < ?",
        (cutoff,),
    ).fetchall()
    ids = [r["id"] for r in rows]
    if ids:
        qmarks = ",".join("?" * len(ids))
        conn.execute(
            f"UPDATE queries SET status='error', status_changed_at=? WHERE id IN ({qmarks})",
            (ts, *ids),
        )
    return ids


# --- runs ------------------------------------------------------------------------

def start_run(conn, query_id: int, ts: int | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO runs (query_id, started_at, status) VALUES (?,?, 'running')",
        (query_id, ts or now()),
    )
    return cur.lastrowid


def finish_run(conn, run_id: int, *, status: str, n_found=0, n_new=0,
               n_price_changed=0, n_notified=0, error_message=None,
               ts: int | None = None) -> None:
    conn.execute(
        """UPDATE runs SET finished_at=?, status=?, n_found=?, n_new=?,
           n_price_changed=?, n_notified=?, error_message=? WHERE id=?""",
        (ts or now(), status, n_found, n_new, n_price_changed, n_notified,
         error_message, run_id),
    )


def recent_runs(conn, query_id: int, limit: int = 20) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM runs WHERE query_id=? ORDER BY started_at DESC LIMIT ?",
        (query_id, limit),
    ).fetchall()


# --- listings --------------------------------------------------------------------

def get_listing(conn, query_id: int, site_listing_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM listings WHERE query_id=? AND site_listing_id=?",
        (query_id, site_listing_id),
    ).fetchone()


def insert_listing(conn, query_id: int, lst, ts: int | None = None) -> int:
    ts = ts or now()
    cur = conn.execute(
        """INSERT INTO listings
           (query_id, site_listing_id, title, last_price, currency, url,
            shipping_available, location, image_url, posted_at, raw_json,
            first_seen_at, last_seen_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (query_id, lst.site_listing_id, lst.title, lst.price, lst.currency, lst.url,
         None if lst.shipping_available is None else int(lst.shipping_available),
         lst.location, lst.image_url, lst.posted_at, json.dumps(lst.raw), ts, ts),
    )
    return cur.lastrowid


def touch_listing(conn, listing_id: int, ts: int | None = None) -> None:
    conn.execute("UPDATE listings SET last_seen_at=? WHERE id=?", (ts or now(), listing_id))


def update_listing_price(conn, listing_id: int, price: float | None,
                         ts: int | None = None) -> None:
    conn.execute(
        "UPDATE listings SET last_price=?, last_seen_at=? WHERE id=?",
        (price, ts or now(), listing_id),
    )


def list_listings(conn, query_id: int, limit: int = 100) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM listings WHERE query_id=? ORDER BY last_seen_at DESC LIMIT ?",
        (query_id, limit),
    ).fetchall()


# --- notifications ---------------------------------------------------------------

def log_notification(conn, *, query_id: int, site_listing_id: str, channel: str,
                     kind: str, status: str, error: str | None = None,
                     ts: int | None = None) -> None:
    conn.execute(
        """INSERT INTO notifications
           (query_id, site_listing_id, channel, kind, status, error, sent_at)
           VALUES (?,?,?,?,?,?,?)""",
        (query_id, site_listing_id, channel, kind, status, error, ts or now()),
    )
