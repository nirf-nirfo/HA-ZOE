"""Tests for the reply-with-quote + emoji-reaction features.

Covers:
- ParsedMessage carries the inbound wamid for text/audio/image/button inbounds,
- send_message/send_interactive_message include `context.message_id` when
  reply_to is set, and omit it otherwise,
- send_reaction posts the correct `type: reaction` payload,
- the reactions handler marks turn_context.reply_handled so _handle_message
  can suppress its text send,
- the handler returns a non-handling message when the inbound has no wamid
  (scheduled check-in path), so the LLM falls back to a text reply.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import turn_context, whatsapp
from app.handlers.reactions import _handle_reaction_call


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# -------- ParsedMessage carries message_id ---------------------------------


def _payload(msg_type: str, extra: dict) -> dict:
    return {
        "entry": [{"changes": [{"value": {"messages": [
            {"id": "wamid.TEST123", "from": "9720", "type": msg_type, **extra},
        ]}}]}]
    }


def _fresh_seen_store(monkeypatch, tmp_path):
    """extract_message dedups by seen-ids on disk — give each test a fresh
    file so the wamid isn't flagged as duplicate from a prior test's run."""
    monkeypatch.setattr(whatsapp, "_SEEN_PATH", tmp_path / "seen.json")
    whatsapp._seen_message_ids.clear()


def test_parsed_text_carries_message_id(tmp_path, monkeypatch):
    _fresh_seen_store(monkeypatch, tmp_path)
    parsed = whatsapp.extract_message(_payload("text", {"text": {"body": "hi"}}))
    assert parsed is not None
    assert parsed.text == "hi"
    assert parsed.message_id == "wamid.TEST123"


def test_parsed_audio_carries_message_id(tmp_path, monkeypatch):
    _fresh_seen_store(monkeypatch, tmp_path)
    parsed = whatsapp.extract_message(_payload("audio", {"audio": {"id": "A"}}))
    assert parsed is not None and parsed.message_id == "wamid.TEST123"


def test_parsed_image_carries_message_id(tmp_path, monkeypatch):
    _fresh_seen_store(monkeypatch, tmp_path)
    parsed = whatsapp.extract_message(_payload("image", {"image": {"id": "I"}}))
    assert parsed is not None and parsed.message_id == "wamid.TEST123"


def test_parsed_button_carries_message_id(tmp_path, monkeypatch):
    _fresh_seen_store(monkeypatch, tmp_path)
    parsed = whatsapp.extract_message(_payload("interactive", {
        "interactive": {"type": "button_reply", "button_reply": {"id": "revert:x:y", "title": "t"}}
    }))
    assert parsed is not None and parsed.message_id == "wamid.TEST123"


# -------- context.message_id on outbound sends -----------------------------


def test_send_message_includes_context_when_reply_to(monkeypatch):
    captured: dict = {}

    class _Resp:
        status_code = 200
        text = ""

    class _Client:
        def __init__(self, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers, json):
            captured["json"] = json
            return _Resp()

    monkeypatch.setattr(whatsapp.httpx, "AsyncClient", _Client)
    _run(whatsapp.send_message("972", "hi", reply_to="wamid.ABC"))
    assert captured["json"]["context"] == {"message_id": "wamid.ABC"}


def test_send_message_omits_context_when_no_reply_to(monkeypatch):
    captured: dict = {}

    class _Resp:
        status_code = 200
        text = ""

    class _Client:
        def __init__(self, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers, json):
            captured["json"] = json
            return _Resp()

    monkeypatch.setattr(whatsapp.httpx, "AsyncClient", _Client)
    _run(whatsapp.send_message("972", "hi"))
    assert "context" not in captured["json"]


def test_send_interactive_includes_context_when_reply_to(monkeypatch):
    captured: dict = {}

    class _Resp:
        status_code = 200
        text = ""

    class _Client:
        def __init__(self, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers, json):
            captured["json"] = json
            return _Resp()

    monkeypatch.setattr(whatsapp.httpx, "AsyncClient", _Client)
    _run(whatsapp.send_interactive_message(
        "972", "hi", [{"id": "x", "title": "t"}], reply_to="wamid.ABC"
    ))
    assert captured["json"]["context"] == {"message_id": "wamid.ABC"}


# -------- send_reaction posts the right payload ----------------------------


def test_send_reaction_payload_shape(monkeypatch):
    captured: dict = {}

    class _Resp:
        status_code = 200
        text = ""

    class _Client:
        def __init__(self, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers, json):
            captured["json"] = json
            return _Resp()

    monkeypatch.setattr(whatsapp.httpx, "AsyncClient", _Client)
    _run(whatsapp.send_reaction("972", "wamid.XYZ", "👍"))
    assert captured["json"]["type"] == "reaction"
    assert captured["json"]["reaction"] == {"message_id": "wamid.XYZ", "emoji": "👍"}


# -------- reactions handler contract ---------------------------------------


def test_reaction_handler_marks_reply_handled_when_wamid_present():
    # ContextVars are task-local, so reset / check must happen in the SAME
    # task as the handler. Wrap the whole flow in one coroutine.
    async def _flow():
        turn_context.reset()
        turn_context.set_inbound_message_id("wamid.XYZ")
        with patch("app.handlers.reactions.send_reaction", new=AsyncMock()) as mock_send:
            out = await _handle_reaction_call("972", "react_to_message", {"emoji": "❤️"})
            mock_send.assert_awaited_once_with("972", "wamid.XYZ", "❤️")
        return out, turn_context.reply_handled()

    out, handled = _run(_flow())
    assert handled is True
    assert "❤️" in out


def test_reaction_handler_falls_back_when_no_wamid():
    async def _flow():
        turn_context.reset()
        # No inbound set — simulates a scheduled check-in or similar path.
        out = await _handle_reaction_call("972", "react_to_message", {"emoji": "👍"})
        return out, turn_context.reply_handled()

    out, handled = _run(_flow())
    assert handled is False
    assert "No inbound" in out or "text instead" in out


def test_reaction_handler_rejects_empty_emoji():
    async def _flow():
        turn_context.reset()
        turn_context.set_inbound_message_id("wamid.XYZ")
        with patch("app.handlers.reactions.send_reaction", new=AsyncMock()) as mock_send:
            out = await _handle_reaction_call("972", "react_to_message", {"emoji": "  "})
            mock_send.assert_not_awaited()
        return out, turn_context.reply_handled()

    out, handled = _run(_flow())
    assert handled is False
    assert "emoji" in out.lower()


# -------- turn_context toggles --------------------------------------------


def test_turn_context_reset_clears_all_flags():
    turn_context.set_inbound_message_id("wamid.XYZ")
    turn_context.mark_reply_handled()
    turn_context.reset()
    assert turn_context.get_inbound_message_id() is None
    assert turn_context.reply_handled() is False
