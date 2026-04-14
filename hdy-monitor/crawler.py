"""
Crawler core — PID traversal + add-to-cart stock detection.

Flow per PID:
  1. GET /cart?action=configureproduct&pid=<pid>  → scrape product info
  2. POST add-to-cart API (with login Cookie) → determine stock
  3. Compare with DB snapshot → record changes → notify
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from typing import Any, Optional

import httpx
from bs4 import BeautifulSoup

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

def _build_headers(cookie: Optional[str]) -> dict[str, str]:
    h = dict(HEADERS_BASE)
    if cookie:
        h["Cookie"] = cookie
    return h


async def fetch_product_page(pid: int, cookie: Optional[str]) -> Optional[str]:
    url = f"{BASE_URL}/cart?action=configureproduct&pid={pid}"
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(url, headers=_build_headers(cookie))
            if r.status_code == 200:
                return r.text
    except Exception as e:
        logger.warning("fetch_product_page pid=%s error: %s", pid, e)
    return None


async def try_add_to_cart(pid: int, cookie: Optional[str]) -> str:
    """
    Attempt to add product to cart.
    Returns: "in_stock" | "out_of_stock" | "unknown"
    """
    if not cookie:
        return "unknown"

    url = f"{BASE_URL}/cart"
    data = {
        "action": "add",
        "id": str(pid),
        "qty": "1",
    }
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.post(
                url,
                data=data,
                headers={**_build_headers(cookie), "Content-Type": "application/x-www-form-urlencoded"},
            )
            text = r.text.lower()
            resp_json: Optional[dict] = None
            try:
                resp_json = r.json()
            except Exception:
                pass

            # Detect out-of-stock responses
            oos_keywords = ["out of stock", "sold out", "no stock", "库存不足", "缺货", "已售罄", "无货"]
            if any(kw in text for kw in oos_keywords):
                return "out_of_stock"

            # Successful add-to-cart usually returns a cart object or success flag
            if resp_json:
                if resp_json.get("result") == "success" or resp_json.get("cart_item_added"):
                    return "in_stock"
                if resp_json.get("result") == "error":
                    msg = str(resp_json.get("message", "")).lower()
                    if any(kw in msg for kw in oos_keywords):
                        return "out_of_stock"
                    return "unknown"

            if r.status_code in (200, 201):
                # Heuristic: if page contains cart-item indicators
                if "cart" in text and ("item" in text or "product" in text):
                    return "in_stock"

            return "unknown"
    except Exception as e:
        logger.warning("try_add_to_cart pid=%s error: %s", pid, e)
        return "unknown"


def parse_product(html: str, pid: int) -> dict[str, Any]:
    """Parse product info from the configure-product page."""
    soup = BeautifulSoup(html, "lxml")
    data: dict[str, Any] = {"pid": pid}

    # --- Name ---
    name_tag = (
        soup.find("h1", class_=re.compile(r"product.*name|name.*product", re.I))
        or soup.find("h2", class_=re.compile(r"product.*name|name.*product", re.I))
        or soup.find("h1")
        or soup.find("title")
    )
    data["name"] = name_tag.get_text(strip=True) if name_tag else None

    # --- Price ---
    price_tag = (
        soup.find(class_=re.compile(r"price", re.I))
        or soup.find("span", string=re.compile(r"\$|¥|￥|HK\$", re.I))
    )
    data["price"] = price_tag.get_text(strip=True) if price_tag else None

    # --- Options / specifications ---
    options: dict[str, str] = {}
    for select in soup.find_all("select"):
        label = select.get("name") or select.get("id") or "option"
        selected = select.find("option", selected=True)
        if selected:
            options[label] = selected.get_text(strip=True)
        else:
            first = select.find("option")
            if first:
                options[label] = first.get_text(strip=True)
    if options:
        data["options"] = options

    # --- Stock text (visible on page before add-to-cart) ---
    stock_tag = soup.find(string=re.compile(r"in stock|out of stock|available|库存|有货|无货|缺货", re.I))
    if stock_tag:
        data["stock_text"] = stock_tag.strip()

    # --- All meta tags for extra fields ---
    for meta in soup.find_all("meta"):
        prop = meta.get("property") or meta.get("name")
        content = meta.get("content")
        if prop and content and prop.startswith("og:"):
            data[prop] = content

    return data


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
            interval_ms: int = cfg.get("interval_ms", 1500)
            loop_enabled: bool = cfg.get("loop_enabled", False)
            cookie: Optional[str] = cfg.get("login_cookie")
            notify_channels: dict[str, Any] = cfg.get("notify_channels", {})

            for pid in range(start_pid, end_pid + 1):
                if not state.running:
                    break

                state.current_pid = pid
                try:
                    await _process_pid(pid, cookie, notify_channels)
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
    cookie: Optional[str],
    notify_channels: dict[str, Any],
) -> None:
    html = await fetch_product_page(pid, cookie)
    if html is None:
        logger.debug("No page for pid=%s, skipping", pid)
        return

    raw_data = parse_product(html, pid)
    stock_status = await try_add_to_cart(pid, cookie)

    name = raw_data.get("name")
    price = raw_data.get("price")

    old_snapshot, changed_fields = await database.upsert_product(
        pid=pid,
        name=name,
        price=price,
        stock_status=stock_status,
        raw_data=raw_data,
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
    body = f"产品: {name or pid}\n变化字段:\n{changes_text}"

    await notifier.send_all(notify_channels, title=title, body=body, pid=pid)


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
