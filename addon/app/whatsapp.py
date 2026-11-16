import hashlib
import hmac
from dataclasses import dataclass
from typing import Any

import httpx

from app.logging_config import logger
from app.settings import settings

GRAPH_API_BASE = "https://graph.facebook.com/v20.0"


def verify_signature(raw_body: bytes, signature_header: str | None) -> bool:
    """Validates Meta's X-Hub-Signature-256 header against the app secret."""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(
        settings.whatsapp_app_secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    received = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(expected, received)


import json
import time
from pathlib import Path

_SEEN_TTL = 604800  # 7 days — Meta's full webhook retry window
_SEEN_PATH = Path("/data/seen_messages.json")
_seen_message_ids: dict[str, float] = {}


def _load_seen() -> None:
    if not _SEEN_PATH.exists():
        return
    try:
        data = json.loads(_SEEN_PATH.read_text(encoding="utf-8"))
        cutoff = time.time() - _SEEN_TTL
        _seen_message_ids.update({k: v for k, v in data.items() if v > cutoff})
    except Exception:
        pass


def _save_seen() -> None:
    try:
        _SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SEEN_PATH.write_text(json.dumps(_seen_message_ids), encoding="utf-8")
    except Exception:
        pass


_load_seen()


def _is_duplicate(message_id: str) -> bool:
    now = time.time()
    cutoff = now - _SEEN_TTL
    expired = [k for k, t in _seen_message_ids.items() if t < cutoff]
    for k in expired:
        del _seen_message_ids[k]
    if message_id in _seen_message_ids:
        return True
    _seen_message_ids[message_id] = now
    _save_seen()
    return False


@dataclass
class ParsedMessage:
    sender: str
    text: str | None = None
    audio_id: str | None = None
    image_id: str | None = None
    image_caption: str | None = None
    # Set when the inbound is a tap on an interactive reply button Zoe sent
    # earlier (e.g. the list↔expense convert button). Opaque payload decoded
    # by the agent layer.
    button_id: str | None = None


def extract_message(payload: dict[str, Any]) -> ParsedMessage | None:
    """Pulls one actionable message out of a WhatsApp webhook payload; None if
    the payload has no message or is a Meta redelivery of one already handled."""
    try:
        entry = payload["entry"][0]
        change = entry["changes"][0]["value"]
        messages = change.get("messages")
        if not messages:
            return None
        message = messages[0]
        msg_id = message["id"]
        if _is_duplicate(msg_id):
            logger.info("Skipping duplicate message %s", msg_id)
            return None
        logger.info("Processing message %s", msg_id)
        sender = message["from"]
        msg_type = message.get("type")
        if msg_type == "text":
            return ParsedMessage(sender=sender, text=message["text"]["body"])
        if msg_type == "audio":
            return ParsedMessage(sender=sender, audio_id=message["audio"]["id"])
        if msg_type == "image":
            img = message["image"]
            return ParsedMessage(
                sender=sender, image_id=img["id"], image_caption=img.get("caption"),
            )
        if msg_type == "interactive":
            interactive = message.get("interactive", {})
            if interactive.get("type") == "button_reply":
                br = interactive.get("button_reply", {})
                return ParsedMessage(sender=sender, button_id=br.get("id"))
        return None
    except (KeyError, IndexError, TypeError):
        logger.warning("Could not parse WhatsApp webhook payload: %s", payload)
        return None


async def download_media(media_id: str) -> tuple[bytes, str]:
    """Downloads WhatsApp media (image/audio/document) by its id; returns
    (bytes, mime_type). Two-hop: fetch a signed URL, then fetch the bytes."""
    url = f"{GRAPH_API_BASE}/{media_id}"
    headers = {"Authorization": f"Bearer {settings.whatsapp_access_token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        meta = await client.get(url, headers=headers)
        meta.raise_for_status()
        info = meta.json()
        media_url = info["url"]
        mime_type = info.get("mime_type", "application/octet-stream")
        data = await client.get(media_url, headers=headers)
        data.raise_for_status()
        return data.content, mime_type


async def send_interactive_message(to: str, body: str, buttons: list[dict]) -> None:
    """Sends a WhatsApp interactive reply-button message. `buttons` is a list of
    up to 3 dicts, each {"id": "<opaque payload, max 256 chars>", "title":
    "<label, max 20 chars>"}. On failure Zoe silently loses the button — the
    body text is what the user sees either way, so we fall back to a plain text
    send so the message itself isn't lost."""
    url = f"{GRAPH_API_BASE}/{settings.whatsapp_phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {settings.whatsapp_access_token}"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": b["id"], "title": b["title"]}}
                    for b in buttons[:3]
                ],
            },
        },
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code >= 300:
            logger.error(
                "WhatsApp interactive send failed: HTTP %s %s; falling back to plain text",
                resp.status_code, resp.text,
            )
            await send_message(to, body)
    except httpx.HTTPError as exc:
        logger.error("WhatsApp interactive send failed: %s; falling back to plain text", exc)
        await send_message(to, body)


async def send_message(to: str, text: str) -> None:
    url = f"{GRAPH_API_BASE}/{settings.whatsapp_phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {settings.whatsapp_access_token}"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code >= 300:
            logger.error("WhatsApp send failed: HTTP %s %s", resp.status_code, resp.text)
    except httpx.HTTPError as exc:
        logger.error("WhatsApp send failed: %s", exc)
