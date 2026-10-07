"""Item 06 split: agenda + daily/evening briefing config tool handlers.

_valid_date lives in `handlers/_common.py` — imported from there so anchors,
check_ins, and expenses can share it without a circular dep.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from app import agenda, briefing, senders
from app.handlers._common import _valid_date
from app.settings import settings

_IL_TZ = ZoneInfo("Asia/Jerusalem")


def _household_phones() -> set[str]:
    """Household membership from the add-on options. The guard that keeps
    cross-sender briefing tools from targeting arbitrary numbers."""
    raw = settings.allowed_sender_numbers or ""
    return {n.strip() for n in raw.split(",") if n.strip()}


def _handle_agenda_call(sender: str, tool: str, inp: dict) -> str:
    if tool == "add_agenda_item":
        date = _valid_date(inp.get("date"))
        text = (inp.get("text") or "").strip()
        if not date:
            return "I need a valid date (YYYY-MM-DD) for that agenda item."
        if not text:
            return "What should I add to the agenda?"
        agenda.add_item(sender, date, text)
        return f"Added to the agenda for {date}: {text} ✅"

    if tool == "list_agenda":
        date = _valid_date(inp.get("date")) or datetime.now(_IL_TZ).strftime("%Y-%m-%d")
        items = agenda.items_for_date(sender, date)
        if not items:
            return f"No agenda items for {date}."
        lines = [f"• [{i.id}] {i.text}" for i in items]
        return f"Agenda for {date}:\n" + "\n".join(lines)

    if tool == "remove_agenda_item":
        date = _valid_date(inp.get("date"))
        text = (inp.get("text") or "").strip()
        if not date or not text:
            return "I need both the date and a snippet of the item to remove."
        removed = agenda.remove_item(sender, date, text)
        if removed:
            return f"Removed from {date}'s agenda: {', '.join(removed)} ✅"
        return f"No agenda item matching '{text}' found on {date}."

    if tool == "set_daily_briefing":
        try:
            hour = int(inp["hour"])
            minute = int(inp["minute"])
        except (TypeError, ValueError, KeyError):
            return "I need a valid hour and minute for the morning briefing time."
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return "Hour must be 0-23 and minute 0-59."
        enabled = inp.get("enabled", True)
        briefing.set_config(sender, hour, minute, enabled)
        if enabled:
            return f"Morning briefing set for {hour:02d}:{minute:02d} ✅"
        return "Morning briefing turned off."

    if tool == "set_evening_briefing":
        try:
            hour = int(inp["hour"])
            minute = int(inp["minute"])
        except (TypeError, ValueError, KeyError):
            return "I need a valid hour and minute for the evening briefing time."
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return "Hour must be 0-23 and minute 0-59."
        enabled = inp.get("enabled", True)
        briefing.set_evening_config(sender, hour, minute, enabled)
        if enabled:
            return f"Evening briefing set for {hour:02d}:{minute:02d} ✅"
        return "Evening briefing turned off."

    if tool == "list_household_members":
        members = sorted(_household_phones())
        if not members:
            return "No household members configured."
        lines = [f"• {senders.resolve(p)} ({p})" for p in members]
        return "Household members:\n" + "\n".join(lines)

    if tool == "copy_my_briefing_to":
        target = (inp.get("target_sender") or "").strip()
        if not target:
            return "I need the target phone number (as it appears in allowed_sender_numbers)."
        if target not in _household_phones():
            return (
                f"'{target}' is not a household member. Call list_household_members "
                "first to see valid targets."
            )
        if target == sender:
            return "That's you — nothing to copy."
        mine = briefing.get_config(sender)
        if mine is None:
            return "You don't have a briefing config to copy. Use set_daily_briefing first."
        # Overwrite target with caller's schedule values, but preserve the
        # target's own "already sent today" state so an in-flight day isn't
        # resent (and freshly-created target won't trigger a backlog send).
        existing_target = briefing.get_config(target)
        briefing.set_config(target, mine.hour, mine.minute, mine.enabled)
        briefing.set_evening_config(
            target, mine.evening_hour, mine.evening_minute, mine.evening_enabled
        )
        if existing_target is not None:
            # Preserve dedup state so the loop doesn't resend today.
            briefing.mark_sent(target, existing_target.last_sent_date or "")
            briefing.mark_evening_sent(target, existing_target.last_evening_sent_date or "")
        target_name = senders.resolve(target)
        return (
            f"Copied your briefing schedule to {target_name}: "
            f"morning {mine.hour:02d}:{mine.minute:02d} "
            f"({'on' if mine.enabled else 'off'}), "
            f"evening {mine.evening_hour:02d}:{mine.evening_minute:02d} "
            f"({'on' if mine.evening_enabled else 'off'}) ✅"
        )

    if tool == "set_briefing_for":
        target = (inp.get("target_sender") or "").strip()
        kind = (inp.get("kind") or "").strip().lower()
        if not target:
            return "I need the target phone number (as it appears in allowed_sender_numbers)."
        if target not in _household_phones():
            return (
                f"'{target}' is not a household member. Call list_household_members "
                "first to see valid targets."
            )
        if kind not in ("morning", "evening"):
            return "kind must be 'morning' or 'evening'."
        try:
            hour = int(inp["hour"])
            minute = int(inp["minute"])
        except (TypeError, ValueError, KeyError):
            return f"I need a valid hour and minute for the {kind} briefing time."
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return "Hour must be 0-23 and minute 0-59."
        enabled = inp.get("enabled", True)
        target_name = senders.resolve(target)
        if kind == "morning":
            briefing.set_config(target, hour, minute, enabled)
            if enabled:
                return f"Morning briefing for {target_name} set for {hour:02d}:{minute:02d} ✅"
            return f"Morning briefing for {target_name} turned off."
        briefing.set_evening_config(target, hour, minute, enabled)
        if enabled:
            return f"Evening briefing for {target_name} set for {hour:02d}:{minute:02d} ✅"
        return f"Evening briefing for {target_name} turned off."

    return ""
