"""Item 22: tool handlers for the spam auto-triage system.

All tools are available to interactive turns only — none of them is on
CHECK_IN_ALLOWED_TOOLS (they mutate persisted state: schedule, memory,
dry-run gate). The scanner runs on its own loop; these tools let the
user configure it, review it, and teach it.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app import spam_audit, spam_scan_schedule, spam_sender_memory

_IL_TZ = ZoneInfo("Asia/Jerusalem")


def _handle_spam_call(sender: str, tool: str, inp: dict) -> str:
    if tool == "set_spam_scan_schedule":
        try:
            start = int(inp["start_hour"])
            end = int(inp["end_hour"])
            interval = int(inp["interval_hours"])
        except (TypeError, ValueError, KeyError):
            return "I need start_hour, end_hour, and interval_hours as integers."
        try:
            row = spam_scan_schedule.set_schedule(start, end, interval)
        except ValueError as exc:
            return f"Invalid schedule: {exc}"
        mode = "dry-run (audit only)" if row.dry_run else "LIVE (will move spam)"
        return (
            f"Spam scan: {row.start_hour:02d}:00–{row.end_hour:02d}:00 "
            f"every {row.interval_hours}h, {row.tz} · {mode} ✅"
        )

    if tool == "show_spam_scan_schedule":
        row = spam_scan_schedule.get_schedule()
        if not row.enabled:
            return "Spam scan is disabled."
        last = (
            datetime.fromtimestamp(row.last_scan_at, tz=_IL_TZ).strftime("%Y-%m-%d %H:%M")
            if row.last_scan_at
            else "never"
        )
        mode = "dry-run (audit only)" if row.dry_run else "LIVE (will move spam)"
        return (
            f"Spam scan: {row.start_hour:02d}:00–{row.end_hour:02d}:00 every "
            f"{row.interval_hours}h, {row.tz}. Mode: {mode}. Last scan: {last}."
        )

    if tool == "disable_spam_scan":
        spam_scan_schedule.disable()
        return "Spam scan disabled. The loop will still tick but won't scan."

    if tool == "enable_spam_scan":
        spam_scan_schedule.enable()
        return "Spam scan enabled."

    if tool == "enable_spam_live_mode":
        row = spam_scan_schedule.enable_live_mode()
        return (
            "⚠️ Spam scan is now LIVE. The next scan will move confident spam "
            f"to the Spam folder. Window: {row.start_hour:02d}:00–{row.end_hour:02d}:00 "
            f"every {row.interval_hours}h."
        )

    if tool == "disable_spam_live_mode":
        spam_scan_schedule.disable_live_mode()
        return "Spam scan back in dry-run (audit-only) mode."

    if tool == "show_spam_audit":
        try:
            days = int(inp.get("days", 7))
        except (TypeError, ValueError):
            days = 7
        rows = spam_audit.recent(days=days, limit=30)
        if not rows:
            return f"No spam-scan activity in the last {days} days."
        lines = [f"Last {len(rows)} spam-scan entries (<={days}d):"]
        for e in rows:
            when = datetime.fromtimestamp(e.at, tz=_IL_TZ).strftime("%m-%d %H:%M")
            tag = "DRY " if e.dry_run else ""
            lines.append(
                f"• {when} {tag}{e.action} — {e.verdict}@{e.confidence:.2f} — "
                f"{e.from_addr} · {e.subject[:60]}"
            )
        return "\n".join(lines)

    if tool == "remember_sender_verdict":
        addr = (inp.get("sender") or "").strip()
        verdict = (inp.get("verdict") or "").strip().lower()
        if not addr:
            return "I need a sender email address to remember."
        if verdict not in ("spam", "legit"):
            return "verdict must be 'spam' or 'legit'."
        spam_sender_memory.set_verdict(addr, verdict)  # type: ignore[arg-type]
        return f"Will treat email from {addr} as {verdict} without asking the classifier. ✅"

    if tool == "forget_sender_verdict":
        addr = (inp.get("sender") or "").strip()
        if not addr:
            return "I need a sender email address to forget."
        removed = spam_sender_memory.forget(addr)
        if removed:
            return f"Forgot the stored verdict for {addr}. The classifier will decide next time."
        return f"No stored verdict for {addr} to forget."

    return ""
