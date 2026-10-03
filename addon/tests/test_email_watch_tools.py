"""Item 21: Claude tool-handler tests for the email watch tools.

Covers the five tools' dict shapes and the delete / enable / disable paths —
including the not_found branches, which the model relies on to tell the user
"I couldn't find that watch" instead of claiming success.
"""
from __future__ import annotations

import json

from app import email_watches
from app.handlers.email_watch import _handle_email_watch_call


def _call(tool: str, inp: dict | None = None) -> dict | list:
    out = _handle_email_watch_call("sender1", tool, inp or {})
    return json.loads(out)


# ---------------------------------------------------------- add_email_watch


def test_add_happy_path(tmp_stores):
    row = _call("add_email_watch", {
        "name": "shop receipts",
        "from_contains": "receipt@shop.co.il",
        "interval_minutes": 30,
    })
    assert isinstance(row, dict)
    assert row["name"] == "shop receipts"
    assert row["from_contains"] == "receipt@shop.co.il"
    assert row["interval_minutes"] == 30
    assert row["enabled"] is True
    assert "id" in row and row["id"]


def test_add_requires_name(tmp_stores):
    assert _call("add_email_watch", {"from_contains": "a@x"}) == {"error": "name is required"}
    assert email_watches.count_all() == 0


def test_add_requires_at_least_one_filter(tmp_stores):
    out = _call("add_email_watch", {"name": "nothing"})
    assert "error" in out
    assert "from_contains" in out["error"]
    assert email_watches.count_all() == 0


def test_add_defaults_interval_to_60(tmp_stores):
    row = _call("add_email_watch", {"name": "x", "subject_contains": "receipt"})
    assert row["interval_minutes"] == 60


def test_add_coerces_bad_interval(tmp_stores):
    row = _call("add_email_watch", {
        "name": "x", "subject_contains": "r", "interval_minutes": "oops",
    })
    assert row["interval_minutes"] == 60


# ---------------------------------------------------------- list_email_watches


def test_list_empty(tmp_stores):
    assert _call("list_email_watches") == []


def test_list_shows_added(tmp_stores):
    a = email_watches.add(name="a", from_contains="a@x")
    b = email_watches.add(name="b", subject_contains="receipt")
    out = _call("list_email_watches")
    assert isinstance(out, list)
    assert [row["id"] for row in out] == [a.id, b.id]
    assert out[0]["name"] == "a"
    assert out[1]["subject_contains"] == "receipt"


# ---------------------------------------------------------- delete_email_watch


def test_delete_happy_path(tmp_stores):
    w = email_watches.add(name="x", from_contains="a@x")
    assert _call("delete_email_watch", {"id": w.id}) == {"status": "deleted"}
    assert email_watches.count_all() == 0


def test_delete_not_found(tmp_stores):
    assert _call("delete_email_watch", {"id": "nope"}) == {"status": "not_found"}


def test_delete_requires_id(tmp_stores):
    assert _call("delete_email_watch", {}) == {"error": "id is required"}


# ---------------------------------------------------------- enable / disable


def test_disable_then_enable(tmp_stores):
    w = email_watches.add(name="x", from_contains="a@x")
    assert _call("disable_email_watch", {"id": w.id}) == {"status": "disabled"}
    assert email_watches.get(w.id).enabled is False
    assert _call("enable_email_watch", {"id": w.id}) == {"status": "enabled"}
    assert email_watches.get(w.id).enabled is True


def test_enable_not_found(tmp_stores):
    assert _call("enable_email_watch", {"id": "nope"}) == {"status": "not_found"}


def test_disable_not_found(tmp_stores):
    assert _call("disable_email_watch", {"id": "nope"}) == {"status": "not_found"}


def test_enable_requires_id(tmp_stores):
    assert _call("enable_email_watch", {}) == {"error": "id is required"}


def test_disable_requires_id(tmp_stores):
    assert _call("disable_email_watch", {}) == {"error": "id is required"}


# ---------------------------------------------------------- misc


def test_unknown_tool_returns_empty():
    assert _handle_email_watch_call("sender1", "not-a-tool", {}) == ""


def test_tools_wired_into_dispatch_table():
    """Confirms the DISPATCH_TABLE registration from handlers/__init__.py so a
    future refactor can't silently drop the watch tools."""
    from app.handlers import DISPATCH_TABLE

    for tool in (
        "add_email_watch",
        "list_email_watches",
        "delete_email_watch",
        "enable_email_watch",
        "disable_email_watch",
    ):
        assert tool in DISPATCH_TABLE, f"{tool} must be routable through DISPATCH_TABLE"


def test_watch_tools_absent_from_check_in_allowlist():
    """Watch tools mutate state — a scheduled check-in must not be able to
    silently register / toggle / delete watches."""
    from app.claude_agent import CHECK_IN_ALLOWED_TOOLS, EMAIL_WATCH_TOOLS

    assert CHECK_IN_ALLOWED_TOOLS.isdisjoint(EMAIL_WATCH_TOOLS)
