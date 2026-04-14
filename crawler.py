"""
Crawler core — PID traversal + add-to-cart stock detection.

Flow per PID:
  1. GET /cart/set_config?pid=<pid>  → fetch product info as JSON (requires JWT auth)
  2. POST /cart/add_to_shop (with JWT Authorization header) → determine stock
  3. Compare with DB snapshot → record changes → notify
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import Any, Optional

import httpx

import database
import notifier
from models import CrawlerStatus

logger = logging.getLogger("crawler")

BASE_URL = "https://www.szhdy.com"
HEADERS_BASE = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": BASE_URL + "/",
}

# ---------------------------------------------------------------------------
# Global crawler state
# ---------------------------------------------------------------------------

class _State:
    running: bool = False
    current_pid: Optional[int] = None
    checked_count: int = 0
    changed_count: int = 0
    _task: Optional[asyncio.Task] = None  # type: ignore[type-arg]

state = _State()


def get_status() -> CrawlerStatus:
    return CrawlerStatus(
        running=state.running,
        current_pid=state.current_pid,
        checked_count=state.checked_count,
        changed_count=state.changed_count,
    )


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _build_headers(token: Optional[str]) -> dict[str, str]:
    h = dict(HEADERS_BASE)
    if token:
        h["Authorization"] = f"JWT {token}"
    return h


def _unwrap_body(body: Any) -> dict[str, Any]:
    """Unwrap .data envelope from HDY API responses, returning the inner dict."""
    if not isinstance(body, dict):
        return {}
    inner = body.get("data")
    return inner if isinstance(inner, dict) else body


def _is_success(body: Any) -> bool:
    """Check if HDY API response indicates success (status==200 or code==1)."""
    if not isinstance(body, dict):
        return False
    data = _unwrap_body(body)
    status = data.get("status")
    if status is not None:
        return int(status) == 200
    code = data.get("code")
    if code is not None:
        return int(code) in (1, 200)
    return False


async def fetch_product_config(pid: int, token: Optional[str]) -> Optional[dict[str, Any]]:
    """
    GET /cart/set_config?pid=<pid>
    Returns the parsed JSON body, or None on error.
    """
    if not token:
        return None
    url = f"{BASE_URL}/cart/set_config"
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(
                url,
                params={"pid": pid},
                headers=_build_headers(token),
            )
            if r.status_code == 200:
                try:
                    return r.json()
                except Exception:
                    pass
    except Exception as e:
        logger.warning("fetch_product_config pid=%s error: %s", pid, e)
    return None


def parse_product_config(body: dict[str, Any], pid: int) -> dict[str, Any]:
    """Parse product info from the /cart/set_config JSON response."""
    data: dict[str, Any] = {"pid": pid}

    # Unwrap .data if present
    inner = _unwrap_body(body)
    product = inner.get("product") if isinstance(inner, dict) else {}
    if not isinstance(product, dict):
        product = {}

    data["name"] = product.get("name") or None

    # Region from product group id / servergroup / gid
    region = (
        product.get("servergroup")
        or product.get("product_group")
        or product.get("gid")
        or product.get("group_name")
    )
    data["region"] = str(region) if region is not None else None

    # Billing cycles
    cycles = product.get("cycle") or []
    if isinstance(cycles, list) and cycles:
        first_cycle = cycles[0] if isinstance(cycles[0], dict) else {}
        data["price"] = first_cycle.get("product_price") or None
        data["billingcycle"] = first_cycle.get("billingcycle") or None
        data["billingcycle_zh"] = first_cycle.get("billingcycle_zh") or None
    else:
        data["price"] = None
        data["billingcycle"] = None
        data["billingcycle_zh"] = None

    # Store all cycles as JSON for frontend display/filtering
    if isinstance(cycles, list):
        data["cycles_json"] = json.dumps(cycles, ensure_ascii=False)
    else:
        data["cycles_json"] = None

    # Store full product info
    if product:
        data["product_raw"] = product

    return data


async def try_add_to_cart(pid: int, billingcycle: Optional[str], token: Optional[str]) -> str:
    """
    Attempt to add product to cart via /cart/add_to_shop.
    Returns: "in_stock" | "out_of_stock" | "unknown"
    """
    if not token:
        return "unknown"

    url = f"{BASE_URL}/cart/add_to_shop"
    payload = {
        "pid": str(pid),
        "qty": "1",
        "currencyid": "1",
        "billingcycle": billingcycle or "",
    }
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.post(
                url,
                json=payload,
                headers={**_build_headers(token), "Content-Type": "application/json"},
            )
            body: Any = None
            try:
                body = r.json()
            except Exception:
                pass

            if body is not None and _is_success(body):
                return "in_stock"

            # Check for explicit out-of-stock messages in known message fields
            oos_keywords = ["out of stock", "sold out", "no stock", "库存不足", "缺货", "已售罄", "无货", "nostock"]
            if isinstance(body, dict):
                msg_text = " ".join(
                    str(body.get(k, "")) for k in ("message", "msg", "info", "error")
                ).lower()
                if any(kw in msg_text for kw in oos_keywords):
                    return "out_of_stock"

            if r.status_code in (200, 201) and body is not None:
                return "out_of_stock"

            return "unknown"
    except Exception as e:
        logger.warning("try_add_to_cart pid=%s error: %s", pid, e)
        return "unknown"


# ---------------------------------------------------------------------------
# Main crawler loop
# ---------------------------------------------------------------------------

async def _crawl_loop() -> None:
    state.running = True
    state.checked_count = 0
    state.changed_count = 0

    try:
        while state.running:
            cfg = await database.get_config()
            start_pid: int = cfg.get("start_pid", 1150)
            end_pid: int = cfg.get("end_pid", 1200)
            exec_start_pid: int = cfg.get("exec_start_pid") or start_pid
            interval_ms: int = cfg.get("interval_ms", 1500)
            loop_enabled: bool = cfg.get("loop_enabled", False)
            token: Optional[str] = cfg.get("login_token")
            notify_channels: dict[str, Any] = cfg.get("notify_channels", {})

            # Build PID list: exec_start_pid → end_pid, then start_pid → exec_start_pid-1
            pids = list(range(exec_start_pid, end_pid + 1))
            if exec_start_pid > start_pid:
                pids += list(range(start_pid, exec_start_pid))

            for pid in pids:
                if not state.running:
                    break

                state.current_pid = pid
                try:
                    await _process_pid(pid, token, notify_channels)
                except Exception as e:
                    logger.error("Error processing pid=%s: %s", pid, e)

                state.checked_count += 1

                # Interval with ±200ms jitter
                jitter = random.randint(-200, 200) / 1000.0
                sleep_sec = max(0.1, interval_ms / 1000.0 + jitter)
                await asyncio.sleep(sleep_sec)

            if not loop_enabled or not state.running:
                break

        logger.info("Crawler finished.")
    finally:
        state.running = False
        state.current_pid = None


async def _process_pid(
    pid: int,
    token: Optional[str],
    notify_channels: dict[str, Any],
) -> None:
    body = await fetch_product_config(pid, token)
    if body is None:
        logger.debug("No response for pid=%s, skipping", pid)
        return

    if not _is_success(body):
        logger.debug("Product pid=%s not found or API error, skipping", pid)
        return

    raw_data = parse_product_config(body, pid)
    name = raw_data.get("name")
    price = raw_data.get("price")
    billingcycle = raw_data.get("billingcycle")
    region = raw_data.get("region")
    billingcycle_zh = raw_data.get("billingcycle_zh")
    cycles_json = raw_data.get("cycles_json")

    if not name:
        logger.debug("Product pid=%s has no name, skipping", pid)
        return

    stock_status = await try_add_to_cart(pid, billingcycle, token)

    old_snapshot, changed_fields = await database.upsert_product(
        pid=pid,
        name=name,
        price=price,
        stock_status=stock_status,
        raw_data=raw_data,
        region=region,
        billingcycle_zh=billingcycle_zh,
        cycles_json=cycles_json,
    )

    if not changed_fields:
        return

    state.changed_count += 1
    logger.info("pid=%s changed fields: %s", pid, changed_fields)

    # Record each changed field in change_log
    for field in changed_fields:
        old_val = old_snapshot.get(field) if old_snapshot else None
        if field in ("name", "price", "stock_status"):
            new_val = {"name": name, "price": price, "stock_status": stock_status}.get(field)
        else:
            new_val = raw_data.get(field)
        await database.insert_change(pid, field, old_val, new_val)

    # Build notification
    changes_text = "\n".join(
        f"  {f}: {(old_snapshot or {}).get(f, '(new)')} → "
        f"{raw_data.get(f, {'name': name, 'price': price, 'stock_status': stock_status}.get(f, ''))}"
        for f in changed_fields
    )
    title = f"[库存监控] PID {pid} 字段变化"
    body_text = f"产品: {name or pid}\n变化字段:\n{changes_text}"

    await notifier.send_all(notify_channels, title=title, body=body_text, pid=pid)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_crawler() -> bool:
    if state.running:
        return False
    state._task = asyncio.create_task(_crawl_loop())
    return True


async def stop_crawler() -> None:
    state.running = False
    if state._task and not state._task.done():
        state._task.cancel()
        try:
            await state._task
        except asyncio.CancelledError:
            pass
    state._task = None
