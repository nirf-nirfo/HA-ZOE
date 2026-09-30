"""Item 06 split: anchor tool handlers. Pure move out of main.py."""
from datetime import datetime

from app import anchors
from app.handlers._common import _valid_date


_HEBREW_DAY_LOOKUP = {
    "sunday": "יום ראשון", "monday": "יום שני", "tuesday": "יום שלישי",
    "wednesday": "יום רביעי", "thursday": "יום חמישי", "friday": "יום שישי", "saturday": "שבת",
}


def _handle_anchor_call(tool: str, inp: dict) -> str:
    if tool == "add_anchor":
        day = (inp.get("day") or "").strip().lower()
        text = (inp.get("text") or "").strip()
        time_hhmm = (inp.get("time") or "").strip() or None
        if day not in anchors.DAYS:
            return "I need a valid day of the week (sunday-saturday)."
        if not text:
            return "What is the anchor?"
        if time_hhmm:
            try:
                datetime.strptime(time_hhmm, "%H:%M")
            except ValueError:
                return "Time must be in HH:MM format."
        raw_tags = inp.get("tags") or []
        if not isinstance(raw_tags, list):
            raw_tags = []
        tags = [t.strip().lower() for t in raw_tags if isinstance(t, str) and t.strip()]
        a = anchors.add_anchor(day, text, time_hhmm, tags=tags or None)
        when = f" at {a.time}" if a.time else ""
        return f"Anchor added ✅ — every {day.capitalize()}{when}: {text}"

    if tool == "list_anchors":
        all_a = anchors.list_all()
        if not all_a:
            return "You have no weekly anchors yet."
        # Group by day, in week order starting Sunday.
        lines = []
        for day in anchors.DAYS:
            bucket = sorted(
                [a for a in all_a if a.day == day], key=lambda a: a.time or "00:00"
            )
            if not bucket:
                continue
            lines.append(f"{_HEBREW_DAY_LOOKUP[day]}:")
            for a in bucket:
                prefix = f"{a.time} — " if a.time else ""
                lines.append(f"  • [{a.id}] {prefix}{a.text}")
        return "Weekly anchors:\n" + "\n".join(lines)

    if tool == "remove_anchor":
        query = (inp.get("text") or "").strip()
        if not query:
            return "Which anchor should I remove?"
        matches = anchors.find_matching(query)
        if not matches:
            return f"I couldn't find an anchor matching '{query}'."
        if len(matches) > 1:
            lines = [f"• [{a.id}] {_HEBREW_DAY_LOOKUP[a.day]}: {a.text}" for a in matches]
            return f"Several anchors match '{query}' — which one? Reply with its id:\n" + "\n".join(lines)
        a = matches[0]
        anchors.remove_anchor(a.id)
        return f"Removed anchor ✅ — {_HEBREW_DAY_LOOKUP[a.day]}: {a.text}"

    if tool == "suppress_anchor_for_date":
        query = (inp.get("text") or "").strip()
        date = _valid_date(inp.get("date"))
        if not query or not date:
            return "I need both the anchor and the date (YYYY-MM-DD) to suppress."
        matches = anchors.find_matching(query)
        if not matches:
            return f"I couldn't find an anchor matching '{query}'."
        if len(matches) > 1:
            lines = [f"• [{a.id}] {_HEBREW_DAY_LOOKUP[a.day]}: {a.text}" for a in matches]
            return f"Several anchors match — which one? Reply with its id:\n" + "\n".join(lines)
        a = matches[0]
        anchors.suppress_for_date(a.id, date)
        return f"Suppressed for {date} ✅ — {a.text} won't appear that day."

    return ""
