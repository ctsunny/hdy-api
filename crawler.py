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
import time
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
AUTH_ERROR_KEYWORDS = (
    "请先登录",
    "登录",
    "登录失效",
    "unauthorized",
    "token",
    "jwt",
    "auth",
    "身份验证",
    "认证",
)
# Refresh token every 15 minutes to reduce session expiry impact without frequent re-login.
KEEPALIVE_INTERVAL_SEC = 15 * 60

# ---------------------------------------------------------------------------
# Global crawler state
# ---------------------------------------------------------------------------

class _State:
    running: bool = False
    current_pid: Optional[int] = None
    checked_count: int = 0
    changed_count: int = 0
    notify_paused: bool = False
    auth_error_detected: bool = False
    last_keepalive_at: float = 0.0
    _task: Optional[asyncio.Task] = None  # type: ignore[type-arg]

state = _State()


def get_status() -> CrawlerStatus:
    return CrawlerStatus(
        running=state.running,
        current_pid=state.current_pid,
        checked_count=state.checked_count,
        changed_count=state.changed_count,
        notify_paused=state.notify_paused,
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


def _extract_text_fields(body: Any) -> str:
    if not isinstance(body, dict):
        return ""
    fields = ["message", "msg", "info", "error", "detail", "status_text"]
    nested = _unwrap_body(body)
    text_parts = [str(body.get(k, "")) for k in fields]
    if isinstance(nested, dict):
        text_parts.extend(str(nested.get(k, "")) for k in fields)
    return " ".join(text_parts).lower()


def _is_auth_error_response(status_code: int, body: Any) -> bool:
    if status_code in (401, 403):
        return True
    text = _extract_text_fields(body)
    return bool(text and any(k in text for k in AUTH_ERROR_KEYWORDS))


def _mark_auth_error(pid: int, action: str) -> None:
    state.auth_error_detected = True
    logger.warning("Detected auth/session failure while %s pid=%s", action, pid)


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


def _extract_jwt_token(body: dict[str, Any]) -> str:
    body_data = body.get("data")
    return body_data.get("jwt", "") if isinstance(body_data, dict) else body.get("jwt", "")


def _to_float_price(price: Any) -> Optional[float]:
    if price is None:
        return None
    s = str(price).strip()
    if not s:
        return None
    cleaned = (
        s.replace("¥", "")
        .replace(",", "")
        .replace("￥", "")
        .replace("元", "")
        .strip()
    )
    try:
        return float(cleaned)
    except ValueError:
        return None


def _is_monthly_cycle(billingcycle: Any, billingcycle_zh: Any) -> bool:
    billing_cycle_en = str(billingcycle or "").lower()
    billing_cycle_zh = str(billingcycle_zh or "")
    monthly_en_keys = ("monthly", "month")
    monthly_zh_keys = ("月", "月付", "月租")
    return (
        any(k in billing_cycle_en for k in monthly_en_keys)
        or any(k in billing_cycle_zh for k in monthly_zh_keys)
    )


def _in_price_range(price: Optional[float], pmin: Optional[float], pmax: Optional[float]) -> bool:
    if pmin is None and pmax is None:
        return True
    if price is None:
        return False
    if pmin is not None and price < pmin:
        return False
    if pmax is not None and price > pmax:
        return False
    return True


async def _login_szhdy(username: str, api_key: str) -> Optional[str]:
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.post(
                f"{BASE_URL}/zjmf_api_login",
                json={"username": username, "password": api_key},
                headers={"Content-Type": "application/json"},
            )
            body: dict[str, Any] = {}
            try:
                body = r.json()
            except Exception:
                pass
            jwt_token = _extract_jwt_token(body)
            if r.status_code == 200 and jwt_token:
                return jwt_token
    except Exception as e:
        logger.warning("keepalive login failed for %s: %s", username, e)
    return None


async def _keepalive_or_switch_account(current_token: Optional[str]) -> Optional[str]:
    accounts = await database.get_site_accounts_credentials()
    if not accounts:
        return current_token

    for acc in accounts:
        account_id = acc.get("id")
        username = str(acc.get("username") or "")
        api_key = str(acc.get("api_key") or "")
        if not account_id or not username or not api_key:
            continue

        new_token = await _login_szhdy(username, api_key)
        if not new_token:
            continue

        await database.update_site_account_token(account_id, new_token, activate=True)
        if not acc.get("is_active"):
            logger.warning("Active account expired; switched to backup account: %s", username)
        return new_token

    logger.warning("All saved site accounts failed keepalive refresh, keeping existing token.")
    return current_token


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
                    body = r.json()
                    if _is_auth_error_response(r.status_code, body):
                        _mark_auth_error(pid, "reading")
                        return None
                    return body
                except Exception:
                    pass
            else:
                try:
                    body = r.json()
                except Exception:
                    body = None
                if _is_auth_error_response(r.status_code, body):
                    _mark_auth_error(pid, "reading")
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

            if _is_auth_error_response(r.status_code, body):
                _mark_auth_error(pid, "add_to_cart")
                return "unknown"

            # Check for explicit out-of-stock messages in known message fields
            oos_keywords = ["out of stock", "sold out", "no stock", "库存不足", "缺货", "已售罄", "无货", "nostock"]
            if isinstance(body, dict):
                msg_text = _extract_text_fields(body)
                if any(kw in msg_text for kw in oos_keywords):
                    return "out_of_stock"

            if r.status_code in (200, 201) and body is not None:
                # Do not assume out_of_stock on generic non-success payloads.
                # Real-world responses here include login-expired hints, anti-bot challenges,
                # and temporary backend errors with HTTP 200, which previously caused false "无货" alerts.
                return "unknown"

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
            # Auto-resume notifications at the start of every new round.
            state.notify_paused = False

            cfg = await database.get_config()
            start_pid: int = cfg.get("start_pid", 1150)
            end_pid: int = cfg.get("end_pid", 1200)
            exec_start_pid: int = cfg.get("exec_start_pid") or start_pid
            interval_ms: int = cfg.get("interval_ms", 1500)
            loop_enabled: bool = cfg.get("loop_enabled", False)
            token: Optional[str] = cfg.get("login_token")
            notify_channels: dict[str, Any] = cfg.get("notify_channels", {})
            notify_price_min = cfg.get("notify_price_min")
            notify_price_max = cfg.get("notify_price_max")
            notify_monthly_price_min = cfg.get("notify_monthly_price_min")
            notify_monthly_price_max = cfg.get("notify_monthly_price_max")
            notify_filter_cfg = {
                "notify_price_min": float(notify_price_min) if notify_price_min is not None else None,
                "notify_price_max": float(notify_price_max) if notify_price_max is not None else None,
                "notify_monthly_price_min": (
                    float(notify_monthly_price_min) if notify_monthly_price_min is not None else None
                ),
                "notify_monthly_price_max": (
                    float(notify_monthly_price_max) if notify_monthly_price_max is not None else None
                ),
            }

            now_ts = time.time()
            if state.auth_error_detected or (now_ts - state.last_keepalive_at >= KEEPALIVE_INTERVAL_SEC):
                token = await _keepalive_or_switch_account(token)
                state.last_keepalive_at = now_ts
                state.auth_error_detected = False

            # Build PID list: exec_start_pid → end_pid, then start_pid → exec_start_pid-1
            pids = list(range(exec_start_pid, end_pid + 1))
            if exec_start_pid > start_pid:
                pids += list(range(start_pid, exec_start_pid))

            for pid in pids:
                if not state.running:
                    break

                state.current_pid = pid
                try:
                    await _process_pid(pid, token, notify_channels, notify_filter_cfg)
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
    notify_filter_cfg: dict[str, Optional[float]],
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

    # Record each changed field in change_log (exclude internal tracking keys)
    _SKIP_LOG = {"product_raw", "pid", "billingcycle"}
    for field in changed_fields:
        if field in _SKIP_LOG:
            continue
        old_val = old_snapshot.get(field) if old_snapshot else None
        if field in ("name", "price", "stock_status"):
            new_val = {"name": name, "price": price, "stock_status": stock_status}.get(field)
        else:
            new_val = raw_data.get(field)
        await database.insert_change(pid, field, old_val, new_val)

    # Build notification — skip internal/noisy fields
    _NOTIFY_SKIP = {"product_raw", "pid", "billingcycle"}
    notify_fields = [f for f in changed_fields if f not in _NOTIFY_SKIP]
    if not notify_fields or state.notify_paused:
        return

    price_float = _to_float_price(price)
    is_monthly = _is_monthly_cycle(billingcycle, billingcycle_zh)
    global_min = notify_filter_cfg.get("notify_price_min")
    global_max = notify_filter_cfg.get("notify_price_max")
    monthly_min = notify_filter_cfg.get("notify_monthly_price_min")
    monthly_max = notify_filter_cfg.get("notify_monthly_price_max")
    if is_monthly and (monthly_min is not None or monthly_max is not None):
        if not _in_price_range(price_float, monthly_min, monthly_max):
            logger.debug(
                "Skip notify pid=%s due to monthly price filter price=%s range=[%s,%s]",
                pid, price_float, monthly_min, monthly_max
            )
            return
    elif not _in_price_range(price_float, global_min, global_max):
        logger.debug(
            "Skip notify pid=%s due to price filter price=%s range=[%s,%s]",
            pid, price_float, global_min, global_max
        )
        return

    _FIELD_LABELS: dict[str, str] = {
        "name": "产品名",
        "price": "价格",
        "stock_status": "库存状态",
        "region": "地区",
        "billingcycle_zh": "周期",
        "cycles_json": "价格/周期",
    }

    def _fmt_val(v: Any, field: str) -> str:
        if v is None or v == "" or v == "None":
            return "—"
        if field == "stock_status":
            return {"in_stock": "有货", "out_of_stock": "无货", "unknown": "未知"}.get(str(v), str(v))
        s = str(v)
        return (s[:120] + "…") if len(s) > 120 else s

    def _old_val(field: str) -> Any:
        if old_snapshot is None:
            return None
        # Direct DB columns
        if field in ("name", "price", "stock_status", "region", "billingcycle_zh", "cycles_json"):
            return old_snapshot.get(field)
        # Fall back to serialised raw_data
        try:
            return json.loads(old_snapshot.get("raw_data") or "{}").get(field)
        except Exception:
            return None

    _new_lookup: dict[str, Any] = {"name": name, "price": price, "stock_status": stock_status}

    changes_text = "\n".join(
        f"  {_FIELD_LABELS.get(f, f)}: "
        f"{_fmt_val(_old_val(f), f)} → "
        f"{_fmt_val(_new_lookup.get(f, raw_data.get(f)), f)}"
        for f in notify_fields
    )
    product_url = f"{BASE_URL}/cart?action=configureproduct&pid={pid}"
    title = f"[库存监控] PID {pid} {name or ''}"
    cycle_display = (billingcycle_zh or billingcycle or "").strip()
    cycle_suffix = f" /{cycle_display}" if cycle_display else ""
    body_text = (
        f"商品价格: {_fmt_val(price, 'price')}{cycle_suffix}\n"
        f"直达链接: {product_url}\n"
        f"变化字段:\n{changes_text}"
    )

    await notifier.send_all(notify_channels, title=title, body=body_text, pid=pid)

    # Also notify active visitor users who have enabled notification channels
    visitor_configs = await database.get_active_visitor_notify_configs()
    for vcfg in visitor_configs:
        v_channels = vcfg.get("notify_channels", {})
        if not v_channels:
            continue
        # Apply visitor-specific price filter if configured
        v_price_min = vcfg.get("notify_price_min")
        v_price_max = vcfg.get("notify_price_max")
        v_monthly_min = vcfg.get("notify_monthly_price_min")
        v_monthly_max = vcfg.get("notify_monthly_price_max")
        if is_monthly and (v_monthly_min is not None or v_monthly_max is not None):
            if not _in_price_range(price_float, v_monthly_min, v_monthly_max):
                continue
        elif (v_price_min is not None or v_price_max is not None):
            if not _in_price_range(price_float, v_price_min, v_price_max):
                continue
        await notifier.send_all(v_channels, title=title, body=body_text, pid=pid)


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


def toggle_notify_pause() -> bool:
    """Toggle notification pause. Returns new notify_paused value."""
    state.notify_paused = not state.notify_paused
    return state.notify_paused
