#!/usr/bin/env python3
"""
HDY Agent — Remote scanning client for HDY Monitor.

Polls the central server for tasks, executes PID scans against szhdy.com,
reports detected changes back to the server.

Usage:
    python agent.py
    HDY_AGENT_CONFIG=/path/to/config.json python agent.py

config.json format:
    {
        "server_url": "http://SERVER_IP:PORT/BASE_PATH",
        "token": "AGENT_TOKEN"
    }
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import sys
import time
from pathlib import Path
from typing import Any, Optional

import httpx

AGENT_VERSION = "1.0.0"

CONFIG_PATH = Path(os.environ.get("HDY_AGENT_CONFIG", str(Path(__file__).parent / "config.json")))

HDY_BASE_URL = "https://www.szhdy.com"
HEARTBEAT_INTERVAL = 30  # seconds between heartbeats
SCAN_JITTER_MS = 200      # ±ms random jitter added to each PID scan interval

_HEADERS_BASE = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": HDY_BASE_URL + "/",
}

AUTH_ERROR_KEYWORDS = (
    "请先登录", "登录", "登录失效", "unauthorized", "token", "jwt", "auth",
    "身份验证", "认证",
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("hdy-agent")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise RuntimeError(f"Config file not found: {CONFIG_PATH}")
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------

class _State:
    running: bool = True
    scanning: bool = False
    current_pid: Optional[int] = None
    current_task: dict[str, Any] = {}
    _scan_task: Optional[asyncio.Task] = None  # type: ignore[type-arg]

state = _State()


# ---------------------------------------------------------------------------
# szhdy.com HTTP helpers
# ---------------------------------------------------------------------------

def _hdy_headers(token: Optional[str]) -> dict[str, str]:
    h = dict(_HEADERS_BASE)
    if token:
        h["Authorization"] = f"JWT {token}"
    return h


def _unwrap(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        return {}
    inner = body.get("data")
    return inner if isinstance(inner, dict) else body


def _is_success(body: Any) -> bool:
    if not isinstance(body, dict):
        return False
    data = _unwrap(body)
    status = data.get("status")
    if status is not None:
        return int(status) == 200
    code = data.get("code")
    if code is not None:
        return int(code) in (1, 200)
    return False


def _is_auth_error(status_code: int, body: Any) -> bool:
    if status_code in (401, 403):
        return True
    text = ""
    if isinstance(body, dict):
        for k in ("message", "msg", "info", "error", "detail"):
            v = body.get(k) or _unwrap(body).get(k, "")
            text += str(v).lower() + " "
    return bool(text and any(k in text for k in AUTH_ERROR_KEYWORDS))


def _parse_product(body: dict[str, Any], pid: int) -> Optional[dict[str, Any]]:
    inner = _unwrap(body)
    product = inner.get("product") or {}
    if not isinstance(product, dict):
        return None
    name = product.get("name") or None
    if not name:
        return None
    cycles = product.get("cycle") or []
    price: Optional[str] = None
    billingcycle: Optional[str] = None
    if isinstance(cycles, list) and cycles:
        first = cycles[0] if isinstance(cycles[0], dict) else {}
        price = first.get("product_price") or None
        billingcycle = first.get("billingcycle") or None
    return {"pid": pid, "name": name, "price": price, "billingcycle": billingcycle}


async def _fetch_product(pid: int, login_token: Optional[str]) -> Optional[dict[str, Any]]:
    if not login_token:
        return None
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True, max_redirects=5) as client:
            r = await client.get(
                f"{HDY_BASE_URL}/cart/set_config",
                params={"pid": pid},
                headers=_hdy_headers(login_token),
            )
            if r.status_code == 200:
                body = r.json()
                if _is_auth_error(r.status_code, body):
                    logger.warning("Auth error fetching pid=%s", pid)
                    return None
                if not _is_success(body):
                    return None
                return _parse_product(body, pid)
            if _is_auth_error(r.status_code, None):
                logger.warning("Auth error (HTTP %s) fetching pid=%s", r.status_code, pid)
    except Exception as e:
        logger.debug("fetch_product pid=%s: %s", pid, e)
    return None


async def _check_stock(pid: int, billingcycle: Optional[str], login_token: Optional[str]) -> str:
    if not login_token:
        return "unknown"
    payload = {
        "pid": str(pid),
        "qty": "1",
        "currencyid": "1",
        "billingcycle": billingcycle or "",
    }
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True, max_redirects=5) as client:
            r = await client.post(
                f"{HDY_BASE_URL}/cart/add_to_shop",
                json=payload,
                headers={**_hdy_headers(login_token), "Content-Type": "application/json"},
            )
            body: Any = None
            try:
                body = r.json()
            except Exception:
                pass
            if body is not None and _is_success(body):
                return "in_stock"
            if _is_auth_error(r.status_code, body):
                return "unknown"
            oos_keywords = ["out of stock", "sold out", "no stock", "库存不足", "缺货", "已售罄", "无货"]
            if isinstance(body, dict):
                msg = ""
                for k in ("message", "msg", "info", "error"):
                    msg += str(body.get(k, "")).lower() + " "
                    msg += str(_unwrap(body).get(k, "")).lower() + " "
                if any(kw in msg for kw in oos_keywords):
                    return "out_of_stock"
            return "unknown"
    except Exception as e:
        logger.debug("check_stock pid=%s: %s", pid, e)
    return "unknown"


# ---------------------------------------------------------------------------
# Server communication
# ---------------------------------------------------------------------------

async def _post_server(
    server_url: str,
    token: str,
    path: str,
    body: dict[str, Any],
) -> Optional[dict[str, Any]]:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"{server_url}{path}",
                json=body,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )
            if r.status_code == 200:
                return r.json()
            logger.warning("Server %s returned HTTP %s", path, r.status_code)
    except Exception as e:
        logger.warning("Server request %s error: %s", path, e)
    return None


async def send_heartbeat(server_url: str, token: str) -> Optional[dict[str, Any]]:
    status_str = "scanning" if state.scanning else "online"
    return await _post_server(
        server_url, token, "/api/agent/heartbeat",
        {"version": AGENT_VERSION, "status": status_str},
    )


async def report_change(
    server_url: str,
    token: str,
    pid: int,
    name: Optional[str],
    price: Optional[str],
    stock_status: str,
    changed_fields: list[str],
) -> None:
    await _post_server(
        server_url, token, "/api/agent/report",
        {
            "pid": pid,
            "name": name,
            "price": price,
            "stock_status": stock_status,
            "changed_fields": changed_fields,
        },
    )


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------

async def do_upgrade(server_url: str, token: str) -> None:
    """Download new agent.py from server and restart the process."""
    logger.info("Upgrade command received, downloading new agent script...")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(
                f"{server_url}/api/agent/script",
                headers={"Authorization": f"Bearer {token}"},
            )
            if r.status_code != 200:
                logger.error("Upgrade download failed: HTTP %s", r.status_code)
                return
            script_text = r.text

        script_path = Path(__file__).resolve()
        # Write to temp file then replace atomically
        tmp = script_path.parent / (script_path.name + ".tmp")
        tmp.write_text(script_text, encoding="utf-8")
        tmp.replace(script_path)
        logger.info("Agent script updated to new version, restarting...")
        os.execv(sys.executable, [sys.executable, str(script_path)])
    except Exception as e:
        logger.error("Upgrade failed: %s", e)


# ---------------------------------------------------------------------------
# Scan loop
# ---------------------------------------------------------------------------

async def run_scan(server_url: str, token: str, task: dict[str, Any]) -> None:
    state.scanning = True
    start_pid = int(task.get("start_pid", 1150))
    end_pid = int(task.get("end_pid", 1200))
    interval_ms = max(500, int(task.get("interval_ms", 1500)))
    loop_enabled = bool(task.get("loop_enabled", False))
    login_token: Optional[str] = task.get("login_token") or None
    custom_pids: Optional[list[int]] = task.get("custom_pids") or None

    # Determine the PID sequence to iterate
    if custom_pids:
        pid_sequence = list(custom_pids)
        logger.info(
            "Starting scan: custom PIDs %s (count=%d), interval=%dms, loop=%s",
            pid_sequence[:5], len(pid_sequence), interval_ms, loop_enabled,
        )
    else:
        pid_sequence = list(range(start_pid, end_pid + 1))
        logger.info(
            "Starting scan: pid %d–%d, interval=%dms, loop=%s",
            start_pid, end_pid, interval_ms, loop_enabled,
        )

    snapshots: dict[int, dict[str, Optional[str]]] = {}

    try:
        while state.scanning:
            for pid in pid_sequence:
                if not state.scanning:
                    break
                state.current_pid = pid

                product = await _fetch_product(pid, login_token)
                if product:
                    stock = await _check_stock(pid, product.get("billingcycle"), login_token)
                    prev = snapshots.get(pid, {})
                    changed: list[str] = []
                    for field in ("name", "price"):
                        if prev.get(field) != product.get(field):
                            changed.append(field)
                    if prev.get("stock_status") != stock:
                        changed.append("stock_status")
                    product["stock_status"] = stock
                    snapshots[pid] = {
                        "name": product.get("name"),
                        "price": product.get("price"),
                        "stock_status": stock,
                    }
                    if changed:
                        logger.info("pid=%s changed: %s", pid, changed)
                        await report_change(
                            server_url, token, pid,
                            product.get("name"), product.get("price"), stock, changed,
                        )

                jitter = random.randint(-SCAN_JITTER_MS, SCAN_JITTER_MS) / 1000.0
                # Jitter is applied on top of the base interval; never sleep less than 0.3s
                await asyncio.sleep(max(0.3, interval_ms / 1000.0) + jitter)

            if not loop_enabled:
                logger.info("Scan round complete (loop disabled), going idle.")
                break

        logger.info("Scan loop ended.")
    except asyncio.CancelledError:
        logger.info("Scan loop cancelled.")
    finally:
        state.scanning = False
        state.current_pid = None


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

async def main() -> None:
    cfg = load_config()
    server_url: str = cfg["server_url"].rstrip("/")
    token: str = cfg["token"]

    logger.info("HDY Agent v%s starting, server=%s", AGENT_VERSION, server_url)

    last_task: dict[str, Any] = {}

    while state.running:
        resp = await send_heartbeat(server_url, token)

        if resp:
            command: str = resp.get("command", "idle")
            task: dict[str, Any] = resp.get("task") or {}

            if command == "upgrade":
                if state.scanning:
                    state.scanning = False
                    if state._scan_task and not state._scan_task.done():
                        state._scan_task.cancel()
                        try:
                            await state._scan_task
                        except asyncio.CancelledError:
                            pass
                await do_upgrade(server_url, token)
                # If upgrade fails we continue the loop
                last_task = {}

            elif command == "scan":
                task_changed = (
                    task.get("start_pid") != last_task.get("start_pid")
                    or task.get("end_pid") != last_task.get("end_pid")
                    or task.get("loop_enabled") != last_task.get("loop_enabled")
                    or task.get("interval_ms") != last_task.get("interval_ms")
                    or task.get("custom_pids") != last_task.get("custom_pids")
                )
                if not state.scanning or task_changed:
                    # Stop existing scan if running
                    if state.scanning:
                        state.scanning = False
                        if state._scan_task and not state._scan_task.done():
                            state._scan_task.cancel()
                            try:
                                await state._scan_task
                            except asyncio.CancelledError:
                                pass
                    last_task = dict(task)
                    state.current_task = task
                    state._scan_task = asyncio.create_task(
                        run_scan(server_url, token, task)
                    )

            elif command == "idle":
                if state.scanning:
                    state.scanning = False
                    if state._scan_task and not state._scan_task.done():
                        state._scan_task.cancel()
                        try:
                            await state._scan_task
                        except asyncio.CancelledError:
                            pass
                last_task = {}

        await asyncio.sleep(HEARTBEAT_INTERVAL)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Agent stopped.")
