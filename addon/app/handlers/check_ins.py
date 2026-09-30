"""Item 06 split: check-in tool handlers. Pure move out of main.py."""
from datetime import datetime
from zoneinfo import ZoneInfo

from app import check_ins

_IL_TZ = ZoneInfo("Asia/Jerusalem")


def _handle_check_in_call(sender: str, tool: str, inp: dict) -> str:
    if tool == "schedule_check_in":
        prompt = (inp.get("prompt") or "").strip()
        if not prompt:
            return "I need a prompt telling me what to check and what to ask."
        try:
            dt = datetime.fromisoformat(inp["when"])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_IL_TZ)
            when = dt.timestamp()
        except (ValueError, KeyError):
            return "I couldn't parse the datetime — please try again."
        if when <= datetime.now(tz=_IL_TZ).timestamp():
            when_str = dt.astimezone(_IL_TZ).strftime("%d/%m/%Y %H:%M")
            return f"That time ({when_str}) is in the past — nothing to schedule."
        recurrence = inp.get("recurrence")
        if recurrence not in check_ins.RECURRENCES:
            recurrence = None
        interval_minutes = inp.get("interval_minutes")
        try:
            interval_minutes = int(interval_minutes) if interval_minutes is not None else None
        except (TypeError, ValueError):
            interval_minutes = None
        if interval_minutes is not None and interval_minutes < 15:
            interval_minutes = 15  # loop only ticks once/minute; guard against runaway
        c = check_ins.add(sender, prompt, when, recurrence, interval_minutes)
        when_str = datetime.fromtimestamp(c.next_at, tz=_IL_TZ).strftime("%d/%m/%Y %H:%M")
        if interval_minutes:
            cadence = f" (every {interval_minutes} min)"
        elif recurrence:
            cadence = f" (repeats {recurrence})"
        else:
            cadence = ""
        return f"Check-in scheduled ✅{cadence} — first at {when_str}."

    if tool == "list_check_ins":
        pending = check_ins.list_for_sender(sender)
        if not pending:
            return "You have no pending check-ins."
        lines = []
        for c in pending:
            when = datetime.fromtimestamp(c.next_at, tz=_IL_TZ).strftime("%d/%m/%Y %H:%M")
            if c.interval_minutes:
                cadence = f" [every {c.interval_minutes}m]"
            elif c.recurrence:
                cadence = f" [{c.recurrence}]"
            else:
                cadence = ""
            lines.append(f"• [{c.id}] {when}{cadence} — {c.prompt}")
        return "Check-ins:\n" + "\n".join(lines)

    if tool == "cancel_check_in":
        query = (inp.get("text") or "").strip()
        if not query:
            return "Which check-in should I cancel?"
        matches = check_ins.find_matching(sender, query)
        if not matches:
            return f"I couldn't find a check-in matching '{query}'."
        if len(matches) > 1:
            lines = [f"• [{c.id}] {c.prompt}" for c in matches]
            return f"Several check-ins match '{query}' — which one? Reply with its id:\n" + "\n".join(lines)
        c = matches[0]
        check_ins.remove(c.id, sender)
        return f"Cancelled ✅ — {c.prompt}"

    return ""
