"""Item 22: tool-handler dispatch."""
import pytest

from app import spam_scan_schedule, spam_sender_memory
from app.handlers.spam import _handle_spam_call

SENDER = "972501111111"


def test_set_spam_scan_schedule_happy_path(tmp_stores):
    out = _handle_spam_call(
        SENDER, "set_spam_scan_schedule",
        {"start_hour": 8, "end_hour": 20, "interval_hours": 2},
    )
    assert "08:00" in out and "20:00" in out
    assert spam_scan_schedule.get_schedule().start_hour == 8


def test_set_spam_scan_schedule_rejects_bad_window(tmp_stores):
    out = _handle_spam_call(
        SENDER, "set_spam_scan_schedule",
        {"start_hour": 20, "end_hour": 8, "interval_hours": 2},
    )
    assert "Invalid schedule" in out


def test_show_spam_scan_schedule(tmp_stores):
    out = _handle_spam_call(SENDER, "show_spam_scan_schedule", {})
    assert "dry-run" in out  # default state
    assert "never" in out    # no scan has run yet


def test_enable_disable_spam_scan(tmp_stores):
    assert "disabled" in _handle_spam_call(SENDER, "disable_spam_scan", {}).lower()
    assert spam_scan_schedule.get_schedule().enabled is False
    assert "enabled" in _handle_spam_call(SENDER, "enable_spam_scan", {}).lower()


def test_live_mode_toggle_messaging(tmp_stores):
    out = _handle_spam_call(SENDER, "enable_spam_live_mode", {})
    assert "LIVE" in out
    assert spam_scan_schedule.get_schedule().dry_run is False
    out = _handle_spam_call(SENDER, "disable_spam_live_mode", {})
    assert "dry-run" in out
    assert spam_scan_schedule.get_schedule().dry_run is True


def test_show_spam_audit_empty(tmp_stores):
    out = _handle_spam_call(SENDER, "show_spam_audit", {"days": 7})
    assert "No spam-scan activity" in out


def test_remember_sender_verdict(tmp_stores):
    out = _handle_spam_call(
        SENDER, "remember_sender_verdict",
        {"sender": "promos@shop.com", "verdict": "spam"},
    )
    assert "promos@shop.com" in out
    assert spam_sender_memory.get("promos@shop.com").verdict == "spam"


def test_remember_rejects_bad_verdict(tmp_stores):
    out = _handle_spam_call(
        SENDER, "remember_sender_verdict",
        {"sender": "x@y.com", "verdict": "maybe"},
    )
    assert "spam" in out and "legit" in out


def test_forget_sender_verdict(tmp_stores):
    spam_sender_memory.set_verdict("x@y.com", "spam")
    out = _handle_spam_call(SENDER, "forget_sender_verdict", {"sender": "x@y.com"})
    assert "Forgot" in out
    assert spam_sender_memory.get("x@y.com") is None


def test_unknown_tool_returns_empty(tmp_stores):
    assert _handle_spam_call(SENDER, "no_such_tool", {}) == ""
