"""Item 06 split: agenda + daily/evening briefing config tool handlers.

_valid_date lives in `handlers/_common.py` — imported from there so anchors,
check_ins, and expenses can share it without a circular dep.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from app import agenda, briefing
from app.handlers._common import _valid_date

_IL_TZ = ZoneInfo("Asia/Jerusalem")


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

    return ""
