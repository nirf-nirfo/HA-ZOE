"""Item 21: Claude tool handlers for the email watch store.

Kept in its own module so the existing read-only email handler
(`handlers/email.py`) stays a thin async dispatcher over IMAP, and the
state-mutating watch tools live beside each other for easy audit.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime

from app import email_watches


def _watch_to_dict(w: email_watches.EmailWatch) -> dict:
    """Serialises a watch to the dict shape returned to Claude. Mirrors
    `asdict` but normalises `last_checked_at` to an ISO string regardless of
    whether the stored row carried a datetime or an already-serialised str."""
    d = asdict(w)
    ts = d.get("last_checked_at")
    if isinstance(ts, datetime):
        d["last_checked_at"] = ts.isoformat()
    return d


def _handle_email_watch_call(sender: str, tool: str, inp: dict) -> str:  # noqa: ARG001
    """Returns a JSON-encoded tool result string for one of the watch tools.

    sender is accepted for signature compatibility with the dispatcher; watches
    are household-wide so the sender doesn't filter the result set.
    """
    if tool == "add_email_watch":
        name = (inp.get("name") or "").strip()
        if not name:
            return json.dumps({"error": "name is required"})
        from_contains = (inp.get("from_contains") or "").strip()
        subject_contains = (inp.get("subject_contains") or "").strip()
        body_contains = (inp.get("body_contains") or "").strip()
        if not (from_contains or subject_contains or body_contains):
            return json.dumps(
                {"error": "at least one of from_contains/subject_contains/body_contains is required"}
            )
        try:
            interval_minutes = int(inp.get("interval_minutes") or 60)
        except (TypeError, ValueError):
            interval_minutes = 60
        if interval_minutes < 1:
            interval_minutes = 1
        w = email_watches.add(
            name=name,
            from_contains=from_contains,
            subject_contains=subject_contains,
            body_contains=body_contains,
            interval_minutes=interval_minutes,
        )
        return json.dumps(_watch_to_dict(w), ensure_ascii=False)

    if tool == "list_email_watches":
        watches = email_watches.list_watches()
        return json.dumps(
            [_watch_to_dict(w) for w in watches],
            ensure_ascii=False,
        )

    if tool == "delete_email_watch":
        watch_id = (inp.get("id") or "").strip()
        if not watch_id:
            return json.dumps({"error": "id is required"})
        ok = email_watches.delete(watch_id)
        return json.dumps({"status": "deleted" if ok else "not_found"})

    if tool == "enable_email_watch":
        watch_id = (inp.get("id") or "").strip()
        if not watch_id:
            return json.dumps({"error": "id is required"})
        ok = email_watches.enable(watch_id)
        return json.dumps({"status": "enabled" if ok else "not_found"})

    if tool == "disable_email_watch":
        watch_id = (inp.get("id") or "").strip()
        if not watch_id:
            return json.dumps({"error": "id is required"})
        ok = email_watches.disable(watch_id)
        return json.dumps({"status": "disabled" if ok else "not_found"})

    return ""
