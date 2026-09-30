"""Item 06 split: monitor tool handlers. Pure move out of main.py."""
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from app import monitors

_IL_TZ = ZoneInfo("Asia/Jerusalem")


def _handle_monitor_call(sender: str, tool: str, inp: dict, known_entities: dict) -> str:
    if tool == "monitor_device":
        entity_id = inp.get("entity_id")
        entity_def = known_entities.get(entity_id)
        if entity_def is None:
            return "That device isn't in the known list — I can't monitor it."
        expected = (inp.get("expected_state") or "").strip()
        alert_text = (inp.get("alert_text") or "").strip()
        if not expected or not alert_text:
            return "I need both the expected state and the alert message to set up monitoring."
        try:
            interval_minutes = float(inp.get("interval_minutes"))
        except (TypeError, ValueError):
            return "How often should I check? Give an interval in minutes."
        if interval_minutes < 1:
            interval_minutes = 1  # the loop ticks once a minute
        try:
            dt = datetime.fromisoformat(inp["until"])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_IL_TZ)
            until = dt.timestamp()
        except (ValueError, KeyError):
            return "Until when should I keep checking? Please give an end date/time."
        if until <= time.time():
            return "That end time is already in the past — nothing to monitor."

        monitors.add_monitor(
            sender, entity_id, entity_def["name"], expected, alert_text, interval_minutes, until
        )
        until_str = dt.astimezone(_IL_TZ).strftime("%d/%m/%Y %H:%M")
        every = f"{interval_minutes:g} min" if interval_minutes < 60 else f"{interval_minutes / 60:g} h"
        return (
            f"Monitoring {entity_def['name']} every {every} until {until_str}; I'll message you "
            f"whenever it isn't '{expected}'."
        )

    if tool == "list_monitors":
        active = monitors.list_monitors(sender)
        if not active:
            return "You have no active monitors."
        lines = []
        for m in active:
            until_str = datetime.fromtimestamp(m.until, tz=_IL_TZ).strftime("%d/%m/%Y %H:%M")
            every = f"{m.interval_minutes:g} min" if m.interval_minutes < 60 else f"{m.interval_minutes / 60:g} h"
            lines.append(f"• [{m.id}] {m.entity_name}: every {every} until {until_str}, expect '{m.expected_state}'")
        return "Active monitors:\n" + "\n".join(lines)

    if tool == "cancel_monitor":
        query = (inp.get("text") or "").strip()
        if not query:
            return "Which monitor should I stop?"
        matches = monitors.find_matching(sender, query)
        if not matches:
            return f"I couldn't find a monitor matching '{query}'."
        if len(matches) > 1:
            lines = [f"• [{m.id}] {m.entity_name}" for m in matches]
            return f"Several monitors match '{query}' — which one? Reply with its id:\n" + "\n".join(lines)
        m = matches[0]
        monitors.delete_monitor(m.id, sender)
        return f"Stopped monitoring {m.entity_name} ✅"

    return ""
