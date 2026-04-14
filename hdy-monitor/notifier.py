"""
Multi-channel notifier.

Each channel reads its settings from a dict that lives in the database
config row under notify_channels[<channel_name>].

Usage:
    from notifier import send_all
    await send_all(notify_channels_dict, pid=1150, title="库存变化", body="...")
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger("notifier")

# ---------------------------------------------------------------------------
# Individual senders
# ---------------------------------------------------------------------------

async def _telegram(cfg: dict[str, Any], title: str, body: str) -> bool:
    token = cfg.get("bot_token", "")
    chat_id = cfg.get("chat_id", "")
    if not token or not chat_id:
        return False
    text = f"*{title}*\n{body}"
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
        return r.status_code == 200


async def _wecom(cfg: dict[str, Any], title: str, body: str) -> bool:
    """企业微信机器人"""
    webhook = cfg.get("webhook_url", "")
    if not webhook:
        return False
    payload = {"msgtype": "text", "text": {"content": f"{title}\n{body}"}}
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(webhook, json=payload)
        return r.status_code == 200


async def _dingtalk(cfg: dict[str, Any], title: str, body: str) -> bool:
    """钉钉机器人（支持加签）"""
    webhook = cfg.get("webhook_url", "")
    secret = cfg.get("secret", "")
    if not webhook:
        return False

    url = webhook
    if secret:
        ts = str(round(time.time() * 1000))
        sign_str = f"{ts}\n{secret}"
        sign = hmac.new(secret.encode(), sign_str.encode(), digestmod=hashlib.sha256).digest()
        import base64, urllib.parse
        sign_b64 = urllib.parse.quote_plus(base64.b64encode(sign).decode())
        url = f"{webhook}&timestamp={ts}&sign={sign_b64}"

    payload = {
        "msgtype": "text",
        "text": {"content": f"{title}\n{body}"},
        "at": {"isAtAll": False},
    }
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(url, json=payload)
        return r.status_code == 200


async def _feishu(cfg: dict[str, Any], title: str, body: str) -> bool:
    """飞书机器人"""
    webhook = cfg.get("webhook_url", "")
    if not webhook:
        return False
    payload = {
        "msg_type": "post",
        "content": {
            "post": {
                "zh_cn": {
                    "title": title,
                    "content": [[{"tag": "text", "text": body}]],
                }
            }
        },
    }
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(webhook, json=payload)
        return r.status_code == 200


async def _serverchan(cfg: dict[str, Any], title: str, body: str) -> bool:
    """Server酱 (微信推送)"""
    sendkey = cfg.get("sendkey", "")
    if not sendkey:
        return False
    url = f"https://sctapi.ftqq.com/{sendkey}.send"
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(url, data={"title": title, "desp": body})
        return r.status_code == 200


async def _bark(cfg: dict[str, Any], title: str, body: str) -> bool:
    """Bark (iOS)"""
    device_key = cfg.get("device_key", "")
    if not device_key:
        return False
    url = f"https://api.day.app/{device_key}/{title}/{body}"
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url)
        return r.status_code == 200


async def _pushplus(cfg: dict[str, Any], title: str, body: str) -> bool:
    token = cfg.get("token", "")
    if not token:
        return False
    payload = {"token": token, "title": title, "content": body, "template": "txt"}
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post("https://www.pushplus.plus/send", json=payload)
        return r.status_code == 200


async def _discord(cfg: dict[str, Any], title: str, body: str) -> bool:
    webhook = cfg.get("webhook_url", "")
    if not webhook:
        return False
    payload = {"embeds": [{"title": title, "description": body}]}
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(webhook, json=payload)
        return r.status_code in (200, 204)


async def _email(cfg: dict[str, Any], title: str, body: str) -> bool:
    try:
        import aiosmtplib
        from email.mime.text import MIMEText

        host = cfg.get("host", "")
        port = int(cfg.get("port", 465))
        user = cfg.get("user", "")
        password = cfg.get("password", "")
        to_addr = cfg.get("to", "")
        if not all([host, user, password, to_addr]):
            return False

        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = title
        msg["From"] = user
        msg["To"] = to_addr

        use_tls = cfg.get("use_tls", True)
        await aiosmtplib.send(
            msg,
            hostname=host,
            port=port,
            username=user,
            password=password,
            use_tls=use_tls,
        )
        return True
    except Exception as e:
        logger.error("Email send error: %s", e)
        return False


async def _webhook(cfg: dict[str, Any], title: str, body: str) -> bool:
    """Custom webhook"""
    url = cfg.get("url", "")
    if not url:
        return False
    method = cfg.get("method", "POST").upper()
    headers = cfg.get("headers", {})
    payload_template = cfg.get("payload", None)

    if payload_template:
        payload_str = payload_template.replace("{{title}}", title).replace("{{body}}", body)
        try:
            payload = json.loads(payload_str)
        except Exception:
            payload = {"title": title, "body": body}
    else:
        payload = {"title": title, "body": body}

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.request(method, url, json=payload, headers=headers)
        return r.status_code < 300


# ---------------------------------------------------------------------------
# Channel registry
# ---------------------------------------------------------------------------

_CHANNELS: dict[str, Any] = {
    "telegram": _telegram,
    "wecom": _wecom,
    "dingtalk": _dingtalk,
    "feishu": _feishu,
    "serverchan": _serverchan,
    "bark": _bark,
    "pushplus": _pushplus,
    "discord": _discord,
    "email": _email,
    "webhook": _webhook,
}


async def send_channel(
    channel: str, cfg: dict[str, Any], title: str, body: str
) -> bool:
    handler = _CHANNELS.get(channel)
    if handler is None:
        logger.warning("Unknown notification channel: %s", channel)
        return False
    try:
        return await handler(cfg, title, body)
    except Exception as e:
        logger.error("Channel %s error: %s", channel, e)
        return False


async def send_all(
    notify_channels: dict[str, Any],
    title: str,
    body: str,
    pid: int | None = None,
) -> None:
    """Send to every enabled channel and record results in notify_log."""
    from database import insert_notify_log  # avoid circular at module level

    for channel, cfg in notify_channels.items():
        if not isinstance(cfg, dict) or not cfg.get("enabled", False):
            continue
        success = await send_channel(channel, cfg, title, body)
        status = "✓" if success else "✗"
        logger.info("[notify] %s %s pid=%s", status, channel, pid)
        await insert_notify_log(channel=channel, message=body, success=success, pid=pid)
