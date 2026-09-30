"""Item 06 split: briefing data gathering + rendering + compile dispatch.

Moved verbatim out of `main.py`. See there for design rationale on the
gather/render split (Item 10). No behavior changes — pure refactor.
"""
import asyncio
import re
from datetime import datetime
from typing import Any

from zoneinfo import ZoneInfo

from app import agenda, anchors, expenses, holidays, personal_tasks
from app import reminders as reminders_mod
from app.lists import get_list
from app.logging_config import logger
from app.memory import all_facts
from app.settings import settings

_IL_TZ = ZoneInfo("Asia/Jerusalem")


# Python's weekday() returns Monday=0..Sunday=6; anchors use Sunday..Saturday strings.
_DAY_NAMES = {6: "sunday", 0: "monday", 1: "tuesday", 2: "wednesday", 3: "thursday", 4: "friday", 5: "saturday"}
_HEBREW_DAY_NAMES = {6: "יום ראשון", 0: "יום שני", 1: "יום שלישי", 2: "יום רביעי", 3: "יום חמישי", 4: "יום שישי", 5: "שבת"}


def _day_key(dt: datetime) -> str:
    return _DAY_NAMES[dt.weekday()]


def _hebrew_day(dt: datetime) -> str:
    return _HEBREW_DAY_NAMES[dt.weekday()]


def _school_note(status: str) -> str:
    if status == "off":
        return " (חופש מבית הספר)"
    if status == "ceremony":
        return " (טקס בבית הספר)"
    return ""


# --- Item 10: gather/render split so briefings can flip between the deterministic
# Python renderer (default, unchanged output) and a memory-aware LLM composition
# (behind settings.briefing_model_compose). Data gathering is the shared, pure
# step; both paths consume the same dict.

# Memory-fact pattern: "המספר <phone> הוא של <name>" (or the loose variant
# "המספר של <name> הוא <phone>"). Best-effort resolution until Item 13 lands
# app.senders.resolve() — swap the body here when that PR lands.
_PHONE_TO_NAME_A = re.compile(r"המספר\s+(\d[\d\s\-+]{4,})\s+הוא\s+של\s+([^\.\n,]+)")
_PHONE_TO_NAME_B = re.compile(r"המספר\s+של\s+([^\.\n,]+?)\s+הוא\s+(\d[\d\s\-+]{4,})")


def _resolve_sender_name(sender: str) -> str | None:
    """Best-effort: pull a name for `sender` out of memory facts.

    Item 13 will replace this with senders.resolve(sender). Until then we
    scan the facts for either phrasing and match on a normalized phone.
    """
    norm_sender = re.sub(r"\D", "", sender or "")
    if not norm_sender:
        return None
    for f in all_facts():
        for pattern, phone_group, name_group in (
            (_PHONE_TO_NAME_A, 1, 2),
            (_PHONE_TO_NAME_B, 2, 1),
        ):
            m = pattern.search(f.text)
            if not m:
                continue
            phone_norm = re.sub(r"\D", "", m.group(phone_group))
            if phone_norm and (phone_norm == norm_sender or phone_norm.endswith(norm_sender[-9:])
                               or norm_sender.endswith(phone_norm[-9:])):
                return m.group(name_group).strip()
    return None


def _memory_facts_texts() -> list[str]:
    return [f.text for f in all_facts()]


def _anchor_dicts(anchor_list) -> list[dict[str, Any]]:
    return [{"text": a.text, "time": a.time} for a in anchor_list]


def _last_month_expense_summary() -> dict[str, Any] | None:
    """Returns last month's expense summary dict when non-empty, else None.

    Kept identical to the deterministic renderer's contract: on the 1st of
    the month, if there were expenses last month, include a top-cats block.
    """
    s = expenses.summary(period="last_month")
    if s["count"] == 0:
        return None
    return {
        "start": s["start"],
        "end": s["end"],
        "total_ils": s["total"],
        "count": s["count"],
        "top_categories": [
            {"category": cat, "total_ils": amt} for cat, amt in list(s["by_category"].items())[:3]
        ],
    }


def _fmt_ils(x: float) -> str:
    return f"₪{x:,.0f}" if float(x).is_integer() else f"₪{x:,.2f}"


async def _gather_day_data(sender: str, dt: datetime) -> dict[str, Any]:
    """Assembles the per-date data block used by both morning and evening
    briefings. Pure aside from disk reads and the holidays fetch — same
    inputs give the same output at a moment in time.

    School-off suppression: if any holiday for this date has
    school_status == "off", anchors tagged "school" are dropped from the
    output and a school_off_note is added so both the deterministic
    renderer and the LLM compose path can surface the reason."""
    date = dt.strftime("%Y-%m-%d")
    day_key = _day_key(dt)
    anchor_list = sorted(anchors.anchors_for_date(day_key, date), key=lambda a: a.time or "00:00")
    yearly = reminders_mod.yearly_for_date(sender, dt.month, dt.day)
    agenda_items = agenda.items_for_date(sender, date)
    hols = await holidays.holidays_for_date(date)

    school_off_holiday = next((h for h in hols if h.get("school_status") == "off"), None)
    school_off_note: str | None = None
    if school_off_holiday is not None:
        before = len(anchor_list)
        anchor_list = [a for a in anchor_list if "school" not in (a.tags or [])]
        if before != len(anchor_list):
            school_off_note = f"🎒 {school_off_holiday['title']} — אין בית ספר היום"

    data: dict[str, Any] = {
        "date": date,
        "hebrew_day": _hebrew_day(dt),
        "anchors": _anchor_dicts(anchor_list),
        "yearly_reminders": [{"text": r.text} for r in yearly],
        "agenda_items": [{"text": i.text} for i in agenda_items],
        "holidays": [{"title": h["title"], "school_status": h["school_status"]} for h in hols],
    }
    if school_off_note:
        data["school_off_note"] = school_off_note
    return data


async def _gather_morning_data(sender: str, dt: datetime) -> dict[str, Any]:
    """Full data blob for a morning brief: today's per-date data + household
    tasks + this sender's personal tasks + memory facts + optional 1st-of-
    month last-month expense summary."""
    day = await _gather_day_data(sender, dt)
    household_open = get_list("tasks")
    my_tasks = personal_tasks.list_for(sender)
    data: dict[str, Any] = {
        **day,
        "sender_name": _resolve_sender_name(sender),
        "household_tasks": [{"text": t.text} for t in household_open],
        "personal_tasks": [{"text": t.text} for t in my_tasks],
        "memory_facts": _memory_facts_texts(),
    }
    if dt.day == 1:
        last = _last_month_expense_summary()
        if last is not None:
            data["last_month_summary"] = last
    return data


async def _gather_evening_data(sender: str, today: datetime, tomorrow: datetime) -> dict[str, Any]:
    """Full data blob for an evening brief: today's + tomorrow's per-date
    data, today's household spend, memory facts, sender name."""
    today_day = await _gather_day_data(sender, today)
    tomorrow_day = await _gather_day_data(sender, tomorrow)
    spent = expenses.summary(period="today")
    return {
        **today_day,
        "sender_name": _resolve_sender_name(sender),
        "todays_expenses": {"count": spent["count"], "total_ils": spent["total"]},
        "tomorrow": tomorrow_day,
        "memory_facts": _memory_facts_texts(),
    }


def _render_morning_deterministic(data: dict[str, Any]) -> str:
    """Byte-identical to the pre-Item-10 morning brief — only source of the
    text is this function when the compose flag is off, so any diff here is
    a real behavior change and must be intentional."""
    hebrew_day = data["hebrew_day"]
    anchor_list = data.get("anchors") or []
    yearly = data.get("yearly_reminders") or []
    agenda_items = data.get("agenda_items") or []
    hols = data.get("holidays") or []

    if not (anchor_list or yearly or agenda_items or hols):
        return f"☀️ בוקר טוב! ל{hebrew_day} אין כלום ביומן."

    parts = [f"☀️ בוקר טוב! סדר יום ל{hebrew_day}:"]

    school_off_note = data.get("school_off_note")
    if school_off_note:
        parts.append(school_off_note)

    if anchor_list:
        lines = []
        for a in anchor_list:
            prefix = f"{a['time']} — " if a.get("time") else ""
            lines.append(f"• {prefix}{a['text']}")
        parts.append("⚓ עוגנים:\n" + "\n".join(lines))

    if yearly:
        parts.append("🎂 קבועות:\n" + "\n".join(f"• {r['text']}" for r in yearly))

    if hols:
        lines = [f"• {h['title']}{_school_note(h['school_status'])}" for h in hols]
        parts.append("🕎 חגים:\n" + "\n".join(lines))

    if agenda_items:
        parts.append("📌 היום:\n" + "\n".join(f"• {i['text']}" for i in agenda_items))

    household_open = data.get("household_tasks") or []
    if household_open:
        lines = [f"• {item['text']}" for item in household_open[:8]]
        more = f"\n… ועוד {len(household_open) - 8}" if len(household_open) > 8 else ""
        parts.append("📝 משימות בית פתוחות:\n" + "\n".join(lines) + more)

    my_tasks = data.get("personal_tasks") or []
    if my_tasks:
        lines = [f"• {t['text']}" for t in my_tasks[:8]]
        more = f"\n… ועוד {len(my_tasks) - 8}" if len(my_tasks) > 8 else ""
        parts.append("📌 משימות שלי:\n" + "\n".join(lines) + more)

    last = data.get("last_month_summary")
    if last:
        lines = [
            f"💰 סיכום החודש הקודם ({last['start']} → {last['end']}):",
            f"סה״כ {_fmt_ils(last['total_ils'])} ({last['count']} הוצאות)",
        ]
        top_cats = last.get("top_categories") or []
        if top_cats:
            lines.append("קטגוריות מובילות:")
            for c in top_cats:
                lines.append(f"  • {c['category']}: {_fmt_ils(c['total_ils'])}")
        parts.append("\n".join(lines))

    return "\n\n".join(parts)


def _one_line_items_from_dicts(day_data: dict[str, Any]) -> str:
    bits = []
    for a in day_data.get("anchors") or []:
        bits.append(f"{a['text']} ב-{a['time']}" if a.get("time") else a["text"])
    for r in day_data.get("yearly_reminders") or []:
        bits.append(r["text"])
    for i in day_data.get("agenda_items") or []:
        bits.append(i["text"])
    for h in day_data.get("holidays") or []:
        bits.append(f"{h['title']}{_school_note(h['school_status'])}")
    return ", ".join(bits) if bits else ""


def _render_evening_deterministic(data: dict[str, Any]) -> str:
    """Byte-identical to the pre-Item-10 evening brief."""
    today_summary = _one_line_items_from_dicts(data)
    tomorrow = data.get("tomorrow") or {}
    tomorrow_summary = _one_line_items_from_dicts(tomorrow)

    lines = ["🌙 ערב טוב!"]
    if today_summary:
        lines.append(f"היום היה: {today_summary}")
    spent = data.get("todays_expenses") or {"count": 0, "total_ils": 0}
    if spent.get("count", 0) > 0:
        lines.append(f"💰 הוצאות היום: {spent['count']} · סה״כ {_fmt_ils(spent['total_ils'])}")
    if tomorrow_summary:
        lines.append(f"מחר ({tomorrow.get('hebrew_day', '')}): {tomorrow_summary}")
    else:
        lines.append(f"מחר ({tomorrow.get('hebrew_day', '')}) נקי — אין כלום ביומן.")
    return "\n".join(lines)


def _run_briefing_model(kind: str, data: dict[str, Any]) -> str:
    """Late-bound proxy: tests monkeypatch `app.main.run_briefing_model`, so
    resolve through app.main at call time (main.py re-exports the symbol from
    claude_agent). Falls back to the direct import if app.main isn't loaded.
    """
    try:
        from app import main as _main  # lazy to avoid circular import at load
        return _main.run_briefing_model(kind, data)
    except (ImportError, AttributeError):
        from app.claude_agent import run_briefing_model
        return run_briefing_model(kind, data)


async def _compile_morning_briefing(sender: str, dt: datetime) -> str:
    """Dispatcher. Gathers the data blob, then either hands it to
    run_briefing_model (memory-aware LLM compose) or falls back to the
    deterministic renderer. Flag off = identical output to pre-Item-10.
    A model exception, timeout, or empty reply falls back too, so a bad
    Anthropic call can never hold up the daily brief loop."""
    data = await _gather_morning_data(sender, dt)
    if settings.briefing_model_compose:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(_run_briefing_model, "morning", data),
                timeout=15,
            )
        except Exception:
            logger.exception("Morning briefing model compose failed; falling back to deterministic")
    return _render_morning_deterministic(data)


async def _compile_evening_briefing(sender: str, today: datetime, tomorrow: datetime) -> str:
    data = await _gather_evening_data(sender, today, tomorrow)
    if settings.briefing_model_compose:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(_run_briefing_model, "evening", data),
                timeout=15,
            )
        except Exception:
            logger.exception("Evening briefing model compose failed; falling back to deterministic")
    return _render_evening_deterministic(data)
