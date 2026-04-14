"""SQLite async database layer using aiosqlite."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import aiosqlite

DB_PATH = os.environ.get("HDY_DB_PATH", str(Path(__file__).parent / "hdy_monitor.db"))

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_DDL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS products (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pid             INTEGER UNIQUE NOT NULL,
    name            TEXT,
    price           TEXT,
    stock_status    TEXT,
    raw_data        TEXT,
    first_seen_at   TEXT,
    last_checked_at TEXT,
    last_changed_at TEXT,
    region          TEXT,
    billingcycle_zh TEXT,
    cycles_json     TEXT
);

CREATE TABLE IF NOT EXISTS change_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pid         INTEGER NOT NULL,
    field_name  TEXT NOT NULL,
    old_value   TEXT,
    new_value   TEXT,
    changed_at  TEXT
);

CREATE TABLE IF NOT EXISTS config (
    id               INTEGER PRIMARY KEY CHECK (id = 1),
    start_pid        INTEGER DEFAULT 1150,
    end_pid          INTEGER DEFAULT 1200,
    exec_start_pid   INTEGER DEFAULT NULL,
    interval_ms      INTEGER DEFAULT 1500,
    loop_enabled     INTEGER DEFAULT 0,
    login_cookie     TEXT,
    login_token      TEXT,
    notify_channels  TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS notify_log (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    pid      INTEGER,
    channel  TEXT NOT NULL,
    message  TEXT,
    sent_at  TEXT,
    success  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS site_accounts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    label       TEXT,
    username    TEXT NOT NULL,
    jwt_token   TEXT,
    is_active   INTEGER DEFAULT 0,
    created_at  TEXT
);

-- Ensure the single config row always exists
INSERT OR IGNORE INTO config (id) VALUES (1);
"""


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(_DDL)
        # Migrate: add columns if they don't exist yet
        async with db.execute("PRAGMA table_info(config)") as cur:
            cols = {row[1] async for row in cur}
        if "login_token" not in cols:
            await db.execute("ALTER TABLE config ADD COLUMN login_token TEXT")
        if "exec_start_pid" not in cols:
            await db.execute("ALTER TABLE config ADD COLUMN exec_start_pid INTEGER DEFAULT NULL")
        async with db.execute("PRAGMA table_info(products)") as cur:
            prod_cols = {row[1] async for row in cur}
        if "region" not in prod_cols:
            await db.execute("ALTER TABLE products ADD COLUMN region TEXT")
        if "billingcycle_zh" not in prod_cols:
            await db.execute("ALTER TABLE products ADD COLUMN billingcycle_zh TEXT")
        if "cycles_json" not in prod_cols:
            await db.execute("ALTER TABLE products ADD COLUMN cycles_json TEXT")
        await db.commit()


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

async def get_config() -> dict[str, Any]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM config WHERE id=1") as cur:
            row = await cur.fetchone()
            if row is None:
                return {}
            d = dict(row)
            d["notify_channels"] = json.loads(d.get("notify_channels") or "{}")
            d["loop_enabled"] = bool(d.get("loop_enabled", 0))
            return d


async def update_config(**kwargs: Any) -> None:
    if not kwargs:
        return
    cols = ", ".join(f"{k}=?" for k in kwargs)
    values = list(kwargs.values())

    # Serialize notify_channels if present
    if "notify_channels" in kwargs and isinstance(kwargs["notify_channels"], dict):
        idx = list(kwargs.keys()).index("notify_channels")
        values[idx] = json.dumps(kwargs["notify_channels"])
    if "loop_enabled" in kwargs:
        idx = list(kwargs.keys()).index("loop_enabled")
        values[idx] = int(values[idx])

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE config SET {cols} WHERE id=1", values)
        await db.commit()


# ---------------------------------------------------------------------------
# Product helpers
# ---------------------------------------------------------------------------

async def upsert_product(
    pid: int,
    name: Optional[str],
    price: Optional[str],
    stock_status: Optional[str],
    raw_data: dict[str, Any],
    region: Optional[str] = None,
    billingcycle_zh: Optional[str] = None,
    cycles_json: Optional[str] = None,
) -> tuple[Optional[dict[str, Any]], list[str]]:
    """
    Insert or update a product row.
    Returns (old_snapshot_or_None, list_of_changed_field_names).
    """
    now = datetime.now(timezone.utc).isoformat()
    raw_json = json.dumps(raw_data, ensure_ascii=False)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Fetch existing
        async with db.execute("SELECT * FROM products WHERE pid=?", (pid,)) as cur:
            existing = await cur.fetchone()

        changed_fields: list[str] = []

        if existing is None:
            # First time seeing this PID
            await db.execute(
                """INSERT INTO products
                   (pid, name, price, stock_status, raw_data,
                    first_seen_at, last_checked_at, last_changed_at,
                    region, billingcycle_zh, cycles_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (pid, name, price, stock_status, raw_json, now, now, now,
                 region, billingcycle_zh, cycles_json),
            )
            await db.commit()
            return None, []

        # Compare fields
        old = dict(existing)
        watch = {
            "name": name,
            "price": price,
            "stock_status": stock_status,
        }
        for field, new_val in watch.items():
            if old.get(field) != new_val:
                changed_fields.append(field)

        # Check raw_data changes (any other field)
        # Exclude noisy/redundant keys that should never trigger notifications.
        _RAW_SKIP = {"pid", "product_raw", "billingcycle"}
        old_raw = json.loads(old.get("raw_data") or "{}")
        for k, v in raw_data.items():
            if k in _RAW_SKIP or k in ("name", "price", "stock_status"):
                continue
            old_v = old_raw.get(k)
            # Treat None and "" as equivalent — avoids false positives when a
            # new field was added to parse_product_config after the row was stored.
            if (old_v is None or old_v == "") and (v is None or v == ""):
                continue
            if str(old_v if old_v is not None else "") != str(v if v is not None else ""):
                if k not in changed_fields:
                    changed_fields.append(k)

        last_changed = now if changed_fields else old.get("last_changed_at")

        await db.execute(
            """UPDATE products
               SET name=?, price=?, stock_status=?, raw_data=?,
                   last_checked_at=?, last_changed_at=?,
                   region=?, billingcycle_zh=?, cycles_json=?
               WHERE pid=?""",
            (name, price, stock_status, raw_json, now, last_changed,
             region, billingcycle_zh, cycles_json, pid),
        )
        await db.commit()
        return old, changed_fields


async def get_products(
    limit: int = 200,
    offset: int = 0,
    region: Optional[str] = None,
    stock_status: Optional[str] = None,
    billingcycle: Optional[str] = None,
    price_min: Optional[float] = None,
    price_max: Optional[float] = None,
) -> list[dict[str, Any]]:
    conditions: list[str] = []
    params: list[Any] = []

    if region:
        conditions.append("region LIKE ?")
        params.append(f"%{region}%")
    if stock_status:
        conditions.append("stock_status = ?")
        params.append(stock_status)
    if billingcycle:
        conditions.append("billingcycle_zh = ?")
        params.append(billingcycle)
    if price_min is not None:
        conditions.append("CAST(REPLACE(REPLACE(price, ',', ''), '¥', '') AS REAL) >= ?")
        params.append(price_min)
    if price_max is not None:
        conditions.append("CAST(REPLACE(REPLACE(price, ',', ''), '¥', '') AS REAL) <= ?")
        params.append(price_max)

    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.extend([limit, offset])

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"SELECT * FROM products {where_clause} ORDER BY pid LIMIT ? OFFSET ?", params
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def count_products(
    region: Optional[str] = None,
    stock_status: Optional[str] = None,
    billingcycle: Optional[str] = None,
    price_min: Optional[float] = None,
    price_max: Optional[float] = None,
) -> int:
    conditions: list[str] = []
    params: list[Any] = []

    if region:
        conditions.append("region LIKE ?")
        params.append(f"%{region}%")
    if stock_status:
        conditions.append("stock_status = ?")
        params.append(stock_status)
    if billingcycle:
        conditions.append("billingcycle_zh = ?")
        params.append(billingcycle)
    if price_min is not None:
        conditions.append("CAST(REPLACE(REPLACE(price, ',', ''), '¥', '') AS REAL) >= ?")
        params.append(price_min)
    if price_max is not None:
        conditions.append("CAST(REPLACE(REPLACE(price, ',', ''), '¥', '') AS REAL) <= ?")
        params.append(price_max)

    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(f"SELECT COUNT(*) FROM products {where_clause}", params) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


async def get_product_filter_options() -> dict[str, list[str]]:
    """Return distinct values for filter dropdowns."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        regions: list[str] = []
        cycles: list[str] = []
        async with db.execute(
            "SELECT DISTINCT region FROM products WHERE region IS NOT NULL AND region != '' ORDER BY region"
        ) as cur:
            regions = [row[0] async for row in cur]
        async with db.execute(
            "SELECT DISTINCT billingcycle_zh FROM products WHERE billingcycle_zh IS NOT NULL AND billingcycle_zh != '' ORDER BY billingcycle_zh"
        ) as cur:
            cycles = [row[0] async for row in cur]
        return {"regions": regions, "cycles": cycles}


async def get_product(pid: int) -> Optional[dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM products WHERE pid=?", (pid,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


# ---------------------------------------------------------------------------
# Change log helpers
# ---------------------------------------------------------------------------

async def insert_change(
    pid: int, field_name: str, old_value: Any, new_value: Any
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO change_log (pid, field_name, old_value, new_value, changed_at)
               VALUES (?,?,?,?,?)""",
            (pid, field_name, str(old_value) if old_value is not None else None,
             str(new_value) if new_value is not None else None, now),
        )
        await db.commit()


async def clear_changes() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM change_log")
        await db.commit()


async def get_changes(
    pid: Optional[int] = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[int, list[dict[str, Any]]]:
    offset = (page - 1) * page_size
    where = "WHERE pid=?" if pid is not None else ""
    params_count = (pid,) if pid is not None else ()
    params_data = (pid, page_size, offset) if pid is not None else (page_size, offset)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"SELECT COUNT(*) FROM change_log {where}", params_count
        ) as cur:
            total = (await cur.fetchone())[0]
        async with db.execute(
            f"SELECT * FROM change_log {where} ORDER BY changed_at DESC LIMIT ? OFFSET ?",
            params_data,
        ) as cur:
            rows = await cur.fetchall()
            return total, [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Notify log helpers
# ---------------------------------------------------------------------------

async def insert_notify_log(
    channel: str,
    message: str,
    success: bool,
    pid: Optional[int] = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO notify_log (pid, channel, message, sent_at, success)
               VALUES (?,?,?,?,?)""",
            (pid, channel, message, now, int(success)),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Site accounts helpers
# ---------------------------------------------------------------------------

async def get_site_accounts() -> list[dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, label, username, is_active, created_at FROM site_accounts ORDER BY id"
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def add_site_account(label: str, username: str, jwt_token: str) -> int:
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO site_accounts (label, username, jwt_token, is_active, created_at) VALUES (?,?,?,0,?)",
            (label, username, jwt_token, now),
        )
        await db.commit()
        return cur.lastrowid  # type: ignore[return-value]


async def delete_site_account(account_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM site_accounts WHERE id=?", (account_id,))
        await db.commit()


async def set_active_site_account(account_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE site_accounts SET is_active=0")
        await db.execute("UPDATE site_accounts SET is_active=1 WHERE id=?", (account_id,))
        await db.commit()
        # Sync the active token into config so the crawler can pick it up
        async with db.execute(
            "SELECT jwt_token FROM site_accounts WHERE id=?", (account_id,)
        ) as cur:
            row = await cur.fetchone()
        if row and row[0]:
            await db.execute("UPDATE config SET login_token=? WHERE id=1", (row[0],))
            await db.commit()


async def get_active_site_account_token() -> Optional[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT jwt_token FROM site_accounts WHERE is_active=1 LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None
