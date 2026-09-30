"""Item 06 split: reminder tool handlers. Pure move out of main.py."""
from datetime import datetime
from zoneinfo import ZoneInfo

from app.reminders import (
    RECURRENCES,
    add_reminder,
    delete_all_reminders,
    delete_reminder,
    find_duplicate,
    find_matching,
    list_reminders,
    next_annual_occurrence,
    reschedule,
)

_IL_TZ = ZoneInfo("Asia/Jerusalem")


def _fmt_reminder(r) -> str:
    when = datetime.fromtimestamp(r.send_at, tz=_IL_TZ).strftime("%d/%m/%Y %H:%M")
    return f"• [{r.id}] {when} — {r.text}"


# (key in reminder.recurrence, section title) in display order; one_time = no recurrence.
_REMINDER_GROUPS = [
    ("one_time", "One-time"),
    ("daily", "🔁 Daily"),
    ("weekly", "🔁 Weekly"),
    ("monthly", "🔁 Monthly"),
    ("yearly", "🔁 Yearly"),
]


def _handle_reminder_call(sender: str, tool: str, inp: dict) -> str:
    if tool == "set_reminder":
        try:
            dt = datetime.fromisoformat(inp["send_at"])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_IL_TZ)
            send_at = dt.timestamp()
        except (ValueError, KeyError):
            return "I couldn't parse that date/time — please try again."
        recurrence = inp.get("recurrence")
        if recurrence not in RECURRENCES:
            recurrence = None
        # For a yearly reminder the first fire is, by definition, the next time that
        # month/day/time comes around — derive it in code so a wrong year from the
        # model (e.g. tomorrow's birthday landing on next year) can't slip through.
        if recurrence == "yearly":
            send_at = next_annual_occurrence(send_at)
        if send_at <= datetime.now(tz=_IL_TZ).timestamp():
            when = dt.astimezone(_IL_TZ).strftime("%d/%m/%Y %H:%M")
            return (
                f"That time ({when}) is in the past, so I didn't set the reminder. "
                "Please tell me the date again, including the year."
            )
        existing = find_duplicate(sender, inp["text"], send_at, recurrence)
        if existing:
            when = datetime.fromtimestamp(existing.send_at, tz=_IL_TZ).strftime("%d/%m/%Y %H:%M")
            return f"You already have that reminder set for {when} — keeping the existing one."
        reminder = add_reminder(sender, inp["text"], send_at, recurrence)
        when = datetime.fromtimestamp(reminder.send_at, tz=_IL_TZ).strftime("%d/%m/%Y %H:%M")
        repeat = f" (repeats {recurrence})" if recurrence else ""
        return f"Reminder set ✅{repeat} — I'll message you on {when}: {reminder.text}"

    if tool == "list_reminders":
        kind = inp.get("kind", "all")
        if kind != "one_time" and kind not in RECURRENCES:
            kind = "all"
        pending = list_reminders(sender, kind)
        if not pending:
            if kind == "all":
                return "You have no pending reminders."
            label = "one-time" if kind == "one_time" else kind
            return f"You have no {label} reminders."
        if kind != "all":
            label = "one-time" if kind == "one_time" else kind
            return f"Your {label} reminders:\n" + "\n".join(_fmt_reminder(r) for r in pending)
        # No filter: group by type so recurring reminders don't bury the one-off ones.
        sections = []
        for key, title in _REMINDER_GROUPS:
            bucket = [r for r in pending if (r.recurrence or "one_time") == key]
            if bucket:
                sections.append(f"{title}:\n" + "\n".join(_fmt_reminder(r) for r in bucket))
        return "Your reminders:\n\n" + "\n\n".join(sections)

    if tool == "delete_reminder":
        rid = inp.get("id", "")
        if delete_reminder(rid, sender):
            return f"Reminder {rid} deleted ✅"
        return f"Reminder {rid} not found."

    if tool == "delete_reminder_by_text":
        query = inp.get("text", "").strip()
        if not query:
            return "Which reminder should I cancel?"
        matches = find_matching(sender, query)
        if not matches:
            return f"I couldn't find a reminder matching '{query}'."
        if len(matches) > 1:
            return (
                f"Several reminders match '{query}' — which one? Reply with its id:\n"
                + "\n".join(_fmt_reminder(r) for r in matches)
            )
        r = matches[0]
        delete_reminder(r.id, sender)
        when = datetime.fromtimestamp(r.send_at, tz=_IL_TZ).strftime("%d/%m/%Y %H:%M")
        return f"Cancelled ✅ — {when}: {r.text}"

    if tool == "reschedule_reminder":
        query = inp.get("text", "").strip()
        try:
            dt = datetime.fromisoformat(inp["send_at"])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_IL_TZ)
            send_at = dt.timestamp()
        except (ValueError, KeyError):
            return "I couldn't parse that new date/time — please try again."
        if send_at <= datetime.now(tz=_IL_TZ).timestamp():
            when = dt.astimezone(_IL_TZ).strftime("%d/%m/%Y %H:%M")
            return f"That new time ({when}) is in the past, so I didn't move the reminder."
        if not query:
            return "Which reminder should I move?"
        matches = find_matching(sender, query)
        if not matches:
            return f"I couldn't find a reminder matching '{query}'."
        if len(matches) > 1:
            return (
                f"Several reminders match '{query}' — which one? Reply with its id:\n"
                + "\n".join(_fmt_reminder(r) for r in matches)
            )
        r = matches[0]
        reschedule(r.id, sender, send_at)
        when = datetime.fromtimestamp(send_at, tz=_IL_TZ).strftime("%d/%m/%Y %H:%M")
        return f"Moved ✅ — now {when}: {r.text}"

    if tool == "delete_all_reminders":
        count = delete_all_reminders(sender)
        return f"All {count} reminder(s) deleted ✅" if count else "No reminders to delete."

    return ""
