import asyncio
import base64
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_IL_TZ = ZoneInfo("Asia/Jerusalem")

import httpx
import yaml
from fastapi import Depends, FastAPI, HTTPException, Request, Response

from app.claude_agent import (
    AGENDA_TOOLS,
    ANCHOR_TOOLS,
    BROADCAST_TOOLS,
    CHECK_IN_TOOLS,
    CONVERSATION_TOOLS,
    EXPENSE_TOOLS,
    LIST_TOOLS,
    MEMORY_TOOLS,
    MONITOR_TOOLS,
    PERSONAL_TASK_TOOLS,
    REMINDER_TOOLS,
    SCHEDULED_ACTION_TOOLS,
    get_known_entities,
    initial_context,
    run_briefing_model,
    run_check_in_model,
    run_model,
)
from app.confirmation import make_pending, pop_if_confirmed, store_pending
from app.ha_client import ha_client
from app.logging_config import logger
from app.lists import add_item, clear_list, get_all_list_names, get_list, remove_items
from app.memory import all_facts, forget, remember
from app import (
    agenda, anchors, briefing, check_ins, conversation, conversation_log, expenses,
    holidays, inbound_tracker, monitors, personal_tasks, recurring_expenses, scheduled_actions,
    senders,
)
from app import reminders as reminders_mod
from app.reminders import (
    RECURRENCES,
    add_reminder,
    delete_all_reminders,
    delete_reminder,
    find_duplicate,
    find_matching,
    list_reminders,
    next_annual_occurrence,
    normalize_recurring,
    pop_due,
    reschedule,
)
from app.settings import settings
from app.transcribe import transcribe_audio
from app.whatsapp import download_media, extract_message, send_message, verify_signature

app = FastAPI(title="ZOE")

# Safety cap on the agentic tool-use loop, so a confused turn can't call tools forever.
_MAX_AGENT_ITERS = 6

# --- Item 07: health/status tracking --------------------------------------
# Process-wide start time and per-loop heartbeats consumed by /health and
# /admin/status. `_beat(name, ok=...)` is called by each background loop at
# the top of every tick; anything that raises inside a loop bumps
# consecutive_errors, and a healthy tick resets it to zero.
_STARTUP_TS: float = time.time()
_LOOP_NAMES = (
    "reminder", "monitor", "scheduled_action",
    "daily_briefing", "check_in", "meta_window",
)
_LOOP_HEARTBEAT: dict[str, dict[str, float | int]] = {
    name: {"last_tick_at": 0.0, "consecutive_errors": 0} for name in _LOOP_NAMES
}


def _beat(name: str, ok: bool = True) -> None:
    """Records a loop tick. Never raises — a broken heartbeat must not break its loop."""
    try:
        entry = _LOOP_HEARTBEAT.get(name)
        if entry is None:
            return
        entry["last_tick_at"] = time.time()
        if ok:
            entry["consecutive_errors"] = 0
        else:
            entry["consecutive_errors"] = int(entry["consecutive_errors"]) + 1
    except Exception:
        pass


def _load_version() -> str:
    """Reads the deployed version from addon/config.yaml once at import time."""
    try:
        cfg_path = Path(__file__).parent.parent / "config.yaml"
        with cfg_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return str(data.get("version", "unknown"))
    except Exception:
        return "unknown"


_VERSION = _load_version()

# Cached reachability probes. Anthropic list() and HA HEAD are cheap but not
# free; /health is likely to be polled by an HA dashboard card, so cache both.
_reach_cache: dict[str, tuple[float, bool]] = {}
_ANTHROPIC_TTL = 60.0
_HA_TTL = 30.0


async def _check_anthropic_reachable() -> bool:
    now = time.time()
    ts, cached = _reach_cache.get("anthropic", (0.0, False))
    if ts and now - ts < _ANTHROPIC_TTL:
        return cached
    from app.claude_agent import _client  # local import: avoid cycle at module load
    try:
        await asyncio.wait_for(
            asyncio.to_thread(lambda: _client.models.list()), timeout=3.0
        )
        ok = True
    except Exception:
        ok = False
    _reach_cache["anthropic"] = (now, ok)
    return ok


async def _check_ha_reachable() -> bool:
    now = time.time()
    ts, cached = _reach_cache.get("ha", (0.0, False))
    if ts and now - ts < _HA_TTL:
        return cached
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.head(f"{settings.ha_base_url.rstrip('/')}/api/")
        ok = resp.status_code < 500
    except Exception:
        ok = False
    _reach_cache["ha"] = (now, ok)
    return ok


async def _health_payload() -> dict:
    anthropic_ok = await _check_anthropic_reachable()
    ha_ok = await _check_ha_reachable()
    loops_unhealthy = any(
        int(v["consecutive_errors"]) > 3 for v in _LOOP_HEARTBEAT.values()
    )
    status = "ok" if (anthropic_ok and ha_ok and not loops_unhealthy) else "degraded"
    return {
        "status": status,
        "version": _VERSION,
        "uptime_seconds": int(time.time() - _STARTUP_TS),
        "anthropic_reachable": anthropic_ok,
        "ha_reachable": ha_ok,
        "loops": {k: dict(v) for k, v in _LOOP_HEARTBEAT.items()},
    }


@app.get("/health")
async def health() -> dict:
    """Unauthenticated, high-level liveness/degradation snapshot. No secrets."""
    return await _health_payload()


def _require_lan(request: Request) -> None:
    """FastAPI dep: allow /admin/status only from RFC1918 or loopback callers.
    Cheaper than middleware for a single route and keeps the check colocated."""
    host = request.client.host if request.client else ""
    if not (host.startswith("192.168.") or host.startswith("127.") or host.startswith("10.")):
        raise HTTPException(status_code=403, detail="LAN only")


@app.get("/admin/status", dependencies=[Depends(_require_lan)])
async def admin_status() -> dict:
    """LAN-only. Richer inspection of every store — counts and single summary
    fields only, never full objects. Safe to bookmark from a browser on the
    home network; a public reverse proxy must not be pointed at this route."""
    now = time.time()
    entries = inbound_tracker.all_senders()
    meta_window = {
        sender: {
            "last_inbound_hours_ago": round((now - e.last_inbound_at) / 3600, 2),
            "warned_23h": e.warned_23h,
            "name": senders.resolve(sender),
        }
        for sender, e in entries.items()
    }
    return {
        "health": await _health_payload(),
        "stores": {
            "reminders": {
                "count": reminders_mod.count_all(),
                "next_fire_at": reminders_mod.next_fire_at(),
            },
            "check_ins": {
                "count": check_ins.count_all(),
                "next_fire_at": check_ins.next_fire_at(),
            },
            "expenses": {
                "count": expenses.count_all(),
                "total_this_month": expenses.summary(period="this_month")["total"],
            },
            "anchors": {"count": len(anchors.list_all())},
            "personal_tasks": {"count_by_sender": personal_tasks.count_by_sender()},
            "recurring_expenses": {"count": len(recurring_expenses.list_all())},
            "monitors": {"count": monitors.count_active()},
            "scheduled_actions": {
                "count": scheduled_actions.count_pending(),
                "next_fire_at": scheduled_actions.next_fire_at(),
            },
            "briefing": {"configured_senders": len(briefing.all_configs())},
        },
        "senders": {
            "allowed": sorted(_allowed_senders()),
            "meta_window": meta_window,
        },
    }

# Meta's WhatsApp free-tier 24h window bookkeeping. We warn the user at ~23h so
# they have time to send a quick inbound and reopen the window before outbound
# messages start silently failing. Senders who haven't messaged in >48h are
# considered inactive-by-design and are not pinged.
_META_WINDOW_HOURS = 24
_WARN_AT_HOURS = 23
_INACTIVE_CUTOFF_HOURS = 48
_META_WINDOW_WARNING = (
    "⏰ עוד שעה החלון של Meta נסגר. שלח לי הודעה קצרה (\"היי\") כדי לפתוח אותו מחדש."
)


@app.on_event("startup")
async def startup() -> None:
    fixed = normalize_recurring()
    if fixed:
        logger.info("Self-healed %d yearly reminder(s) to their correct next occurrence", fixed)
    tagged = anchors.auto_tag_school()
    if tagged:
        logger.info("Auto-tagged %d anchor(s) as 'school' from text heuristics", tagged)
    asyncio.create_task(_reminder_loop())
    asyncio.create_task(_monitor_loop())
    asyncio.create_task(_scheduled_action_loop())
    asyncio.create_task(_daily_briefing_loop())
    asyncio.create_task(_check_in_loop())
    asyncio.create_task(_meta_window_loop())


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


async def _gather_day_data(sender: str, dt: datetime) -> dict[str, Any]:
    """Assembles the per-date data block used by both morning and evening
    briefings. Pure aside from disk reads and the holidays fetch — same
    inputs give the same output at a moment in time."""
    date = dt.strftime("%Y-%m-%d")
    day_key = _day_key(dt)
    anchor_list = sorted(anchors.anchors_for_date(day_key, date), key=lambda a: a.time or "00:00")
    yearly = reminders_mod.yearly_for_date(sender, dt.month, dt.day)
    agenda_items = agenda.items_for_date(sender, date)
    hols = await holidays.holidays_for_date(date)
    return {
        "date": date,
        "hebrew_day": _hebrew_day(dt),
        "anchors": _anchor_dicts(anchor_list),
        "yearly_reminders": [{"text": r.text} for r in yearly],
        "agenda_items": [{"text": i.text} for i in agenda_items],
        "holidays": [{"title": h["title"], "school_status": h["school_status"]} for h in hols],
    }


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
                asyncio.to_thread(run_briefing_model, "morning", data),
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
                asyncio.to_thread(run_briefing_model, "evening", data),
                timeout=15,
            )
        except Exception:
            logger.exception("Evening briefing model compose failed; falling back to deterministic")
    return _render_evening_deterministic(data)


async def _daily_briefing_loop() -> None:
    while True:
        await asyncio.sleep(60)
        _beat("daily_briefing")
        try:
            now_il = datetime.now(_IL_TZ)
            today_str = now_il.strftime("%Y-%m-%d")
            tomorrow = now_il + timedelta(days=1)
            for cfg in briefing.all_enabled():
                try:
                    # Morning briefing
                    if cfg.enabled and cfg.last_sent_date != today_str:
                        scheduled_morning = now_il.replace(hour=cfg.hour, minute=cfg.minute, second=0, microsecond=0)
                        if now_il >= scheduled_morning:
                            text = await _compile_morning_briefing(cfg.sender, now_il)
                            await send_message(cfg.sender, text)
                            briefing.mark_sent(cfg.sender, today_str)
                            logger.info("Sent morning briefing to %s for %s", cfg.sender, today_str)
                    # Evening briefing
                    if cfg.evening_enabled and cfg.last_evening_sent_date != today_str:
                        scheduled_eve = now_il.replace(
                            hour=cfg.evening_hour, minute=cfg.evening_minute, second=0, microsecond=0
                        )
                        if now_il >= scheduled_eve:
                            text = await _compile_evening_briefing(cfg.sender, now_il, tomorrow)
                            await send_message(cfg.sender, text)
                            briefing.mark_evening_sent(cfg.sender, today_str)
                            logger.info("Sent evening briefing to %s for %s", cfg.sender, today_str)
                except Exception:
                    logger.exception("Daily briefing loop: failed for %s", cfg.sender)
            # Insert any recurring household bills due today (idempotent).
            try:
                recurring_expenses.insert_due_today()
            except Exception:
                logger.exception("Daily briefing loop: recurring expenses tick failed")
            # Keep stores tidy; nothing before today is ever read again.
            agenda.purge_before(today_str)
            anchors.purge_suppressions_before(today_str)
        except Exception:
            _beat("daily_briefing", ok=False)
            logger.exception("Daily briefing loop: tick failed")


async def _scheduled_action_loop() -> None:
    while True:
        await asyncio.sleep(60)
        _beat("scheduled_action")
        try:
            due = scheduled_actions.pop_due()
        except Exception:
            _beat("scheduled_action", ok=False)
            logger.exception("Scheduled action loop: pop_due failed")
            continue
        for a in due:
            try:
                known_entities = get_known_entities()
                entity_def = known_entities.get(a.entity_id)
                if entity_def and entity_def.get("risky"):
                    # Same yes/confirm gate as an immediate risky action — never auto-run these.
                    pending = make_pending(a.entity_id, a.domain, a.service, a.service_data, a.description)
                    store_pending(a.sender, [pending])
                    logger.info("Scheduled risky action %s due — queued for confirmation", a.id)
                    await send_message(
                        a.sender, f"Time to run: {a.description}. This is sensitive — reply 'yes' to confirm."
                    )
                    continue
                logger.info("Running scheduled action %s: %s", a.id, a.description)
                reply = await _execute_control_action(a.entity_id, a.domain, a.service, a.service_data, a.description)
                if a.duration_minutes and a.service == "turn_on" and "✅" in reply:
                    asyncio.create_task(
                        _auto_turn_off_later(a.sender, a.entity_id, a.domain, a.entity_name, a.duration_minutes)
                    )
                    reply += f" (will auto turn-off in {a.duration_minutes:g} min)"
                await send_message(a.sender, f"⏰ Scheduled action: {reply}")
            except Exception:
                logger.exception("Scheduled action loop: failed to run %s", a.id)


async def _monitor_loop() -> None:
    while True:
        await asyncio.sleep(60)
        _beat("monitor")
        now = time.time()
        try:
            due = monitors.get_due(now)
        except Exception:
            _beat("monitor", ok=False)
            logger.exception("Monitor loop: get_due failed")
            continue
        for m in due:
            try:
                live = await ha_client.get_states([m.entity_id])
                actual = live.get(m.entity_id, {}).get("state", "unknown")
                # Edge-triggered: alert on the first check that's bad, and whenever the device
                # leaves the expected state — but not repeatedly while it stays bad.
                if actual != m.expected_state and (m.last_state is None or m.last_state == m.expected_state):
                    logger.info("Monitor %s: %s is %s (expected %s) — alerting", m.id, m.entity_id, actual, m.expected_state)
                    await send_message(m.sender, m.alert_text)
                monitors.advance(m.id, now + m.interval_minutes * 60, actual)
            except Exception:
                logger.exception("Monitor loop: check failed for %s", m.id)
        try:
            monitors.purge_expired(now)
        except Exception:
            logger.exception("Monitor loop: purge_expired failed")


async def _check_in_loop() -> None:
    while True:
        await asyncio.sleep(60)
        _beat("check_in")
        try:
            due = check_ins.pop_due()
        except Exception:
            _beat("check_in", ok=False)
            logger.exception("Check-in loop: pop_due failed")
            continue
        for c in due:
            try:
                logger.info("Firing check-in %s for %s", c.id, c.sender)
                await _run_check_in(
                    c.sender, c.id, c.prompt, c.last_fired_at, c.last_fired_text
                )
            except Exception:
                logger.exception("Check-in loop: failed to run %s", c.id)


def _prior_check_in_context(
    sender: str, last_fired_at: float | None, last_fired_text: str | None
) -> str:
    """Builds the continuity block for the check-in framing. Empty string when
    there's no prior fire — the model gets no extra text and behaves as before."""
    if not last_fired_at or not last_fired_text:
        return ""
    minutes_ago = max(1, int((time.time() - last_fired_at) / 60))
    lines = [
        f"Your previous ping ({minutes_ago} minutes ago) said: '{last_fired_text}'."
    ]
    # Find the user's reply, if any, by scanning short-term memory for the last
    # assistant turn matching that ping and taking the newest user turn after it.
    # Turns don't carry timestamps, so anchoring on the previous ping's text is
    # the most reliable way to know whether a later user turn was a reply to it.
    try:
        turns = conversation.recent(sender)
    except Exception:
        turns = []
    anchor = -1
    for i, t in enumerate(turns):
        if t.get("role") == "assistant" and t.get("content") == last_fired_text:
            anchor = i
    if anchor >= 0:
        reply = None
        for t in turns[anchor + 1 :]:
            if t.get("role") == "user":
                reply = t.get("content")
        if reply:
            lines.append(f"The user replied: '{reply}'.")
    lines.append("Reference that briefly if it's relevant; don't just repeat the same question.")
    return "\n".join(lines)


async def _run_check_in(
    sender: str,
    check_in_id: str,
    ci_prompt: str,
    last_fired_at: float | None = None,
    last_fired_text: str | None = None,
) -> None:
    """Runs a scheduled check-in as a restricted (read-only) agent turn, then
    delivers the composed message to the sender."""
    known_entities = get_known_entities()
    states = await ha_client.get_states(list(known_entities.keys()))

    prior = _prior_check_in_context(sender, last_fired_at, last_fired_text)
    prior_block = f"\n\n{prior}" if prior else ""
    framing = (
        "[SCHEDULED CHECK-IN — invoked by ZOE's scheduler, not by the user.]\n"
        f"You set up this check-in earlier. Follow this instruction to compose ONE natural "
        f"WhatsApp message to send to the user NOW:\n\n{ci_prompt}{prior_block}\n\n"
        "Read any state you need via read-only tools, then reply with the final message text; "
        "it will be sent to the user verbatim."
    )
    context = initial_context(framing, states, sender)
    messages: list = [{"role": "user", "content": context}]
    final_text = ""

    for _ in range(_MAX_AGENT_ITERS):
        message = await asyncio.to_thread(run_check_in_model, messages)
        tool_uses = [b for b in message.content if b.type == "tool_use"]
        if tool_uses:
            messages.append({"role": "assistant", "content": message.content})
            results = []
            for tu in tool_uses:
                logger.info("Check-in tool call: %s %s", tu.name, tu.input)
                result = await _dispatch_tool(sender, tu.name, tu.input, known_entities, [])
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": result})
            messages.append({"role": "user", "content": results})
            continue
        if message.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": message.content})
            continue
        final_text = "".join(b.text for b in message.content if b.type == "text").strip()
        break

    if not final_text:
        logger.warning("Check-in produced no message for %s (prompt=%r)", sender, ci_prompt)
        return
    await send_message(sender, final_text)
    # Record so a user follow-up ("done", "not yet") has short-term context to resolve against.
    marker = f"[scheduled check-in: {ci_prompt}]"
    conversation.record(sender, marker, final_text)
    conversation_log.append(sender, marker, final_text)
    # Stash this fire on the (rescheduled) check-in row so the next tick can
    # reference it. One-shots that pop_due removed become a no-op here.
    try:
        check_ins.record_fire(check_in_id, final_text)
    except Exception:
        logger.exception("Check-in %s: record_fire failed", check_in_id)


async def _meta_window_loop() -> None:
    """Warns each allowed sender once, at ~23h into Meta's 24h free-tier window,
    so they can send a quick inbound and keep the window open. Senders inactive
    for more than 48h are skipped (they aren't actively conversing)."""
    warn_lo = _WARN_AT_HOURS * 3600
    warn_hi = _META_WINDOW_HOURS * 3600
    inactive_cutoff = _INACTIVE_CUTOFF_HOURS * 3600
    while True:
        await asyncio.sleep(300)
        _beat("meta_window")
        try:
            entries = inbound_tracker.all_senders()
        except Exception:
            _beat("meta_window", ok=False)
            logger.exception("Meta-window loop: all_senders failed")
            continue
        now = time.time()
        allowed = _allowed_senders()
        for sender, entry in entries.items():
            try:
                if sender not in allowed:
                    continue
                elapsed = now - entry.last_inbound_at
                if elapsed >= inactive_cutoff:
                    continue
                if entry.warned_23h:
                    continue
                if warn_lo <= elapsed < warn_hi:
                    logger.info("Meta-window loop: warning %s (%s) at %.1fh", sender, senders.resolve(sender), elapsed / 3600)
                    await send_message(sender, _META_WINDOW_WARNING)
                    inbound_tracker.mark_warned(sender)
            except Exception:
                logger.exception("Meta-window loop: failed for %s", sender)


async def _reminder_loop() -> None:
    while True:
        await asyncio.sleep(60)
        _beat("reminder")
        # Guard the whole tick: a bad reminder or a failed send must never kill the
        # loop, or reminders would silently stop firing forever while the app stays up.
        try:
            due = pop_due()
        except Exception:
            _beat("reminder", ok=False)
            logger.exception("Reminder loop: pop_due failed")
            continue
        for reminder in due:
            try:
                logger.info("Firing reminder %s for %s", reminder.id, reminder.sender)
                await send_message(reminder.sender, f"⏰ {reminder.text}")
            except Exception:
                logger.exception("Reminder loop: failed to send reminder %s", reminder.id)


@app.get("/webhook")
async def verify_webhook(request: Request) -> Response:
    params = request.query_params
    if (
        params.get("hub.mode") == "subscribe"
        and params.get("hub.verify_token") == settings.whatsapp_verify_token
    ):
        return Response(content=params.get("hub.challenge", ""), media_type="text/plain")
    return Response(status_code=403)


@app.post("/webhook")
async def receive_webhook(request: Request) -> Response:
    raw_body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")
    if not verify_signature(raw_body, signature):
        logger.warning("Rejected webhook with invalid signature")
        return Response(status_code=403)

    payload = await request.json()
    parsed = extract_message(payload)
    if parsed is None:
        return Response(status_code=200)

    if parsed.sender not in _allowed_senders():
        logger.warning("Rejected message from unauthorized sender %s", parsed.sender)
        return Response(status_code=200)

    asyncio.create_task(_process_message(parsed))
    return Response(status_code=200)


def _allowed_senders() -> set[str]:
    return {n.strip() for n in settings.allowed_sender_numbers.split(",") if n.strip()}


async def _process_message(parsed) -> None:
    sender = parsed.sender
    # Sender was already verified as allowed in receive_webhook. Refresh the
    # Meta 24h-window tracker on every real inbound so _meta_window_loop can
    # warn the user before the window closes.
    try:
        inbound_tracker.record_inbound(sender)
    except Exception:
        logger.exception("Failed to record inbound for %s", sender)
    text = parsed.text
    image_block = None

    if parsed.audio_id:
        logger.info("Inbound voice from %s, transcribing...", sender)
        text = await transcribe_audio(parsed.audio_id)
        if not text:
            await send_message(sender, "Sorry, I couldn't understand the voice message.")
            return
        logger.info("Transcribed voice from %s: %s", sender, text)

    if parsed.image_id:
        try:
            img_bytes, mime = await download_media(parsed.image_id)
        except Exception:
            logger.exception("Failed to download image from %s", sender)
            await send_message(sender, "Sorry, I couldn't download that image.")
            return
        image_block = {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": mime,
                "data": base64.b64encode(img_bytes).decode(),
            },
        }
        # Caption becomes the accompanying text; if none, give Claude a hint.
        text = parsed.image_caption or text or "(תמונה)"
        logger.info("Inbound image from %s (caption=%r)", sender, parsed.image_caption)

    if text is None:
        return

    logger.info("Inbound from %s: %s", sender, text)
    await _handle_message(sender, text, image_block)


async def _execute_control_action(entity_id: str, domain: str, service: str, service_data: dict, description: str) -> str:
    logger.info("Executing: %s", description)
    success, detail = await ha_client.call_service(domain, service, entity_id, service_data)
    if success:
        return f"{description} ✅"
    return f"Failed: {description} — {detail}"


async def _auto_turn_off_later(sender: str, entity_id: str, domain: str, name: str, minutes: float) -> None:
    await asyncio.sleep(minutes * 60)
    logger.info("Auto turn-off firing for %s after %s minutes", entity_id, minutes)
    success, detail = await ha_client.call_service(domain, "turn_off", entity_id, {})
    if success:
        await send_message(sender, f"{name}: turned off automatically after {minutes:g} min ✅")
    else:
        await send_message(sender, f"{name}: failed to auto turn-off — {detail}")


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


def _handle_list_call(sender: str, tool: str, inp: dict) -> str:
    list_name = inp.get("list_name", "")

    if tool == "add_to_list":
        text = inp.get("text", "").strip()
        if not text:
            return "What should I add to the list?"
        add_item(list_name, text, sender)
        return f"Added to {list_name}: {text} ✅"

    if tool == "remove_from_list":
        text = inp.get("text", "").strip()
        removed = remove_items(list_name, text)
        if removed:
            return f"Removed from {list_name}: {', '.join(removed)} ✅"
        return f"No items matching '{text}' found in {list_name}."

    if tool == "clear_list":
        count = clear_list(list_name)
        return f"{list_name.capitalize()} list cleared ({count} item(s)) ✅"

    if tool == "show_list":
        items = get_list(list_name)
        if not items:
            return f"The {list_name} list is empty."
        lines = [f"• {item.text}" for item in items]
        return f"{list_name.capitalize()} list:\n" + "\n".join(lines)

    if tool == "show_all_lists":
        names = get_all_list_names()
        if not names:
            return "You don't have any lists yet."
        lines = [f"• {name} ({count})" for name, count in names]
        return "Your lists:\n" + "\n".join(lines)

    return ""


def _handle_memory_call(tool: str, inp: dict) -> str:
    text = inp.get("text", "").strip()
    if tool == "remember":
        if not text:
            return "Nothing to remember."
        fact = remember(text)
        if fact is None:
            return f"Already in memory: {text}"
        return f"Remembered: {text}"

    if tool == "forget":
        removed = forget(text)
        if removed:
            return "Forgot: " + "; ".join(removed)
        return f"No remembered fact matching '{text}'."

    return ""


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


def _handle_scheduled_action_call(sender: str, tool: str, inp: dict, known_entities: dict) -> str:
    if tool == "schedule_action":
        entity_id = inp.get("entity_id")
        entity_def = known_entities.get(entity_id)
        if entity_def is None:
            return "That device isn't in the known list — I can't schedule an action on it."
        domain = inp.get("domain")
        service = inp.get("service")
        if not domain or not service:
            return "schedule_action needs both a domain and a service."
        service_data = inp.get("service_data") or {}
        duration_minutes = inp.get("duration_minutes")
        try:
            dt = datetime.fromisoformat(inp["run_at"])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_IL_TZ)
            run_at = dt.timestamp()
        except (ValueError, KeyError):
            return "I couldn't parse that date/time — please try again."
        if run_at <= datetime.now(tz=_IL_TZ).timestamp():
            when = dt.astimezone(_IL_TZ).strftime("%d/%m/%Y %H:%M")
            return f"That time ({when}) is in the past, so I didn't schedule it."

        existing = scheduled_actions.find_duplicate(sender, entity_id, service, service_data, run_at)
        if existing:
            when = datetime.fromtimestamp(existing.run_at, tz=_IL_TZ).strftime("%d/%m/%Y %H:%M")
            return f"You already have that scheduled for {when} — keeping the existing one."

        description = f"{entity_def['name']}: {service}"
        action = scheduled_actions.add_action(
            sender, entity_id, entity_def["name"], domain, service, service_data,
            description, run_at, duration_minutes,
        )
        when = datetime.fromtimestamp(action.run_at, tz=_IL_TZ).strftime("%d/%m/%Y %H:%M")
        risky_note = " Since this device is sensitive, I'll still ask you to confirm at that moment." \
            if entity_def.get("risky") else ""
        return f"Scheduled ✅ — I'll {description} at {when}.{risky_note}"

    if tool == "list_scheduled_actions":
        pending = scheduled_actions.list_actions(sender)
        if not pending:
            return "You have no pending scheduled actions."
        lines = [
            f"• [{a.id}] {datetime.fromtimestamp(a.run_at, tz=_IL_TZ).strftime('%d/%m/%Y %H:%M')} — {a.description}"
            for a in pending
        ]
        return "Scheduled actions:\n" + "\n".join(lines)

    if tool == "cancel_scheduled_action":
        query = (inp.get("text") or "").strip()
        if not query:
            return "Which scheduled action should I cancel?"
        matches = scheduled_actions.find_matching(sender, query)
        if not matches:
            return f"I couldn't find a scheduled action matching '{query}'."
        if len(matches) > 1:
            lines = [f"• [{a.id}] {a.description}" for a in matches]
            return f"Several match '{query}' — which one? Reply with its id:\n" + "\n".join(lines)
        a = matches[0]
        scheduled_actions.delete_action(a.id, sender)
        return f"Cancelled ✅ — {a.description}"

    return ""


def _valid_date(date_str: str | None) -> str | None:
    """Validates an ISO date string, returning it unchanged if valid or None otherwise."""
    if not date_str:
        return None
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return date_str
    except ValueError:
        return None


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


def _fmt_ils(x: float) -> str:
    return f"₪{x:,.0f}" if float(x).is_integer() else f"₪{x:,.2f}"


def _handle_expense_call(sender: str, tool: str, inp: dict) -> str:
    if tool == "add_expense":
        try:
            amount = float(inp["amount"])
        except (KeyError, TypeError, ValueError):
            return "I need a numeric amount."
        if amount <= 0:
            return "Amount must be positive."
        category = inp.get("category")
        if category not in expenses.CATEGORIES:
            return f"Category must be one of: {', '.join(expenses.CATEGORIES)}."
        payment_method = inp.get("payment_method") or "לא צוין"
        if payment_method not in expenses.PAYMENT_METHODS:
            return f"Payment method must be one of: {', '.join(expenses.PAYMENT_METHODS)}."
        description = (inp.get("description") or "").strip()
        date = _valid_date(inp.get("date"))
        # Honor an explicit source from the model; otherwise default to "manual".
        # (Receipt-vs-manual isn't reliably inferable from tool inputs alone.)
        source = inp.get("source") or "manual"
        e = expenses.add(sender, amount, category, payment_method, description, source=source, date=date)
        payment_note = f" ({payment_method})" if payment_method != "לא צוין" else ""
        desc_note = f" — {description}" if description else ""
        return f"נרשם ✅ {_fmt_ils(amount)} · {category}{payment_note}{desc_note} [id: {e.id}]"

    if tool == "delete_last_expense":
        removed = expenses.delete_last_manual(sender)
        if not removed:
            return "אין הוצאה למחיקה 🤷"
        return (
            f"🗑️ נמחק: {_fmt_ils(removed.amount)} — {removed.category}"
            + (f" ({removed.description})" if removed.description else "")
        )

    if tool == "fix_last_expense":
        try:
            new_amount = float(inp["new_amount"])
        except (KeyError, TypeError, ValueError):
            return "I need a numeric new_amount."
        if new_amount <= 0:
            return "Amount must be positive."
        updated = expenses.update_last_amount(sender, new_amount)
        if not updated:
            return "אין הוצאה לתיקון 🤷"
        return f"✏️ תוקן: {_fmt_ils(new_amount)} — {updated.category} ({updated.description or '—'})"

    if tool == "list_recent_expenses":
        try:
            limit = int(inp.get("limit") or 10)
        except (TypeError, ValueError):
            limit = 10
        limit = max(1, min(limit, 50))
        sender_filter = sender if inp.get("sender_only") else None
        recent = expenses.list_recent(limit, sender_filter)
        if not recent:
            return "אין הוצאות רשומות."
        lines = []
        for e in recent:
            who = "" if sender_filter else f" · {senders.resolve(e.sender)}"
            pay = f" · {e.payment_method}" if e.payment_method != "לא צוין" else ""
            desc = f" — {e.description}" if e.description else ""
            lines.append(f"• [{e.id}] {e.date} · {_fmt_ils(e.amount)} · {e.category}{pay}{who}{desc}")
        header = "ההוצאות שלך" if sender_filter else "הוצאות אחרונות (משפחתי)"
        return f"{header}:\n" + "\n".join(lines)

    if tool == "expense_summary":
        period = inp.get("period")
        start = _valid_date(inp.get("start"))
        end = _valid_date(inp.get("end"))
        category = inp.get("category")
        if category and category not in expenses.CATEGORIES:
            return f"Category must be one of: {', '.join(expenses.CATEGORIES)}."
        sender_filter = sender if inp.get("sender_only") else None
        s = expenses.summary(
            period=period, start=start, end=end, category=category, sender=sender_filter,
        )
        if s["count"] == 0:
            return f"אין הוצאות בין {s['start']} ל-{s['end']}."
        lines = [
            f"סיכום {s['start']} → {s['end']}:",
            f"סה״כ: {_fmt_ils(s['total'])} ({s['count']} הוצאות)",
            "",
            "לפי מדווח:",
        ]
        for phone, amt in s["by_sender"].items():
            lines.append(f"  • {senders.resolve(phone)}: {_fmt_ils(amt)}")
        lines.append("")
        lines.append("לפי קטגוריה:")
        for cat, amt in s["by_category"].items():
            pct = int(round(amt / s["total"] * 100)) if s["total"] else 0
            lines.append(f"  • {cat}: {_fmt_ils(amt)} ({pct}%)")
        lines.append("")
        lines.append("לפי אמצעי תשלום:")
        for method, amt in s["by_payment_method"].items():
            lines.append(f"  • {method}: {_fmt_ils(amt)}")
        return "\n".join(lines)

    if tool == "add_recurring_expense":
        name = (inp.get("name") or "").strip()
        if not name:
            return "I need a name for the recurring bill."
        try:
            amount = float(inp["amount"])
        except (KeyError, TypeError, ValueError):
            return "I need a numeric amount."
        try:
            day = int(inp["day_of_month"])
        except (KeyError, TypeError, ValueError):
            return "I need a day_of_month (1-31)."
        if not 1 <= day <= 31:
            return "day_of_month must be between 1 and 31."
        month_pattern = (inp.get("month_pattern") or "monthly").strip() or "monthly"
        category = inp.get("category") or "חשבונות"
        if category not in expenses.CATEGORIES:
            return f"Category must be one of: {', '.join(expenses.CATEGORIES)}."
        payment_method = inp.get("payment_method") or "לא צוין"
        if payment_method not in expenses.PAYMENT_METHODS:
            return f"Payment method must be one of: {', '.join(expenses.PAYMENT_METHODS)}."
        r = recurring_expenses.add(name, amount, day, month_pattern, category, payment_method)
        pattern_note = "" if r.month_pattern == "monthly" else f" (חודשים: {r.month_pattern})"
        return f"🔄 נוספה הוצאה קבועה [{r.id}]: {r.name} — {_fmt_ils(r.amount)} כל {r.day_of_month} לחודש{pattern_note}"

    if tool == "list_recurring_expenses":
        items = recurring_expenses.list_all()
        if not items:
            return "אין הוצאות קבועות רשומות."
        lines = ["🔄 הוצאות קבועות:"]
        for r in items:
            pay = f" · {r.payment_method}" if r.payment_method != "לא צוין" else ""
            pattern_note = "" if r.month_pattern == "monthly" else f" [{r.month_pattern}]"
            lines.append(f"• [{r.id}] {r.name} — {_fmt_ils(r.amount)} (יום {r.day_of_month}){pattern_note}{pay}")
        return "\n".join(lines)

    if tool == "remove_recurring_expense":
        query = (inp.get("text") or "").strip()
        if not query:
            return "Which recurring bill should I remove?"
        matches = recurring_expenses.find_matching(query)
        if not matches:
            return f"לא נמצאה הוצאה קבועה תואמת '{query}'."
        if len(matches) > 1:
            lines = [f"• [{r.id}] {r.name} — {_fmt_ils(r.amount)}" for r in matches]
            return f"כמה הוצאות תואמות '{query}' — איזו? השב עם id:\n" + "\n".join(lines)
        r = matches[0]
        recurring_expenses.remove(r.id)
        return f"🗑️ הוסרה הוצאה קבועה: {r.name} — {_fmt_ils(r.amount)}"

    return ""


def _handle_personal_task_call(sender: str, tool: str, inp: dict) -> str:
    if tool == "add_personal_task":
        text = (inp.get("text") or "").strip()
        if not text:
            return "What personal task should I add?"
        t = personal_tasks.add(sender, text)
        return f"Added to your personal tasks ✅ [{t.id}] {text}"

    if tool == "list_personal_tasks":
        tasks = personal_tasks.list_for(sender)
        if not tasks:
            return "אין לך משימות אישיות פתוחות."
        lines = [f"• [{t.id}] {t.text}" for t in tasks]
        return "המשימות האישיות שלך:\n" + "\n".join(lines)

    if tool == "complete_personal_task":
        query = (inp.get("text") or "").strip()
        if not query:
            return "Which personal task should I mark done?"
        matches = personal_tasks.find_matching(sender, query)
        if not matches:
            return f"I couldn't find a personal task matching '{query}'."
        if len(matches) > 1:
            lines = [f"• [{t.id}] {t.text}" for t in matches]
            return f"Several personal tasks match '{query}' — which one? Reply with its id:\n" + "\n".join(lines)
        t = matches[0]
        personal_tasks.complete(sender, t.id)
        return f"סימנתי שסיימת ✅ — {t.text}"

    if tool == "clear_personal_tasks":
        count = personal_tasks.clear(sender)
        return f"נמחקו {count} משימות אישיות ✅" if count else "אין משימות אישיות למחיקה."

    return ""


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


def _handle_conversation_call(sender: str, tool: str, inp: dict) -> str:
    if tool == "search_past_conversations":
        query = (inp.get("query") or "").strip()
        if not query:
            return "What should I search for?"
        days_back = inp.get("days_back")
        try:
            days_back = int(days_back) if days_back is not None else None
        except (TypeError, ValueError):
            days_back = None
        results = conversation_log.search(sender, query, days_back)
        if not results:
            scope = f" in the last {days_back} days" if days_back else ""
            return f"I couldn't find any past conversation mentioning '{query}'{scope}."
        blocks = []
        for e in results:
            when = datetime.fromtimestamp(e["ts"], tz=_IL_TZ).strftime("%d/%m/%Y %H:%M")
            blocks.append(f"[{when}]\nUser: {e['user']}\nZOE: {e['assistant']}")
        return f"Past exchanges mentioning '{query}' (newest first):\n\n" + "\n\n".join(blocks)

    return ""


async def _dispatch_tool(
    sender: str, tool: str, inp: dict, known_entities: dict, pending_actions: list
) -> str:
    """Executes one tool call and returns a result string fed back to the model.
    Risky device actions are not executed here — they're queued for user confirmation."""
    if tool in REMINDER_TOOLS:
        return _handle_reminder_call(sender, tool, inp)

    if tool in LIST_TOOLS:
        return _handle_list_call(sender, tool, inp)

    if tool in MEMORY_TOOLS:
        return _handle_memory_call(tool, inp)

    if tool in MONITOR_TOOLS:
        return _handle_monitor_call(sender, tool, inp, known_entities)

    if tool in SCHEDULED_ACTION_TOOLS:
        return _handle_scheduled_action_call(sender, tool, inp, known_entities)

    if tool in AGENDA_TOOLS:
        return _handle_agenda_call(sender, tool, inp)

    if tool in ANCHOR_TOOLS:
        return _handle_anchor_call(tool, inp)

    if tool in CONVERSATION_TOOLS:
        return _handle_conversation_call(sender, tool, inp)

    if tool in EXPENSE_TOOLS:
        return _handle_expense_call(sender, tool, inp)

    if tool in CHECK_IN_TOOLS:
        return _handle_check_in_call(sender, tool, inp)

    if tool in PERSONAL_TASK_TOOLS:
        return _handle_personal_task_call(sender, tool, inp)

    entity_id = inp.get("entity_id")
    entity_def = known_entities.get(entity_id)
    if entity_def is None:
        logger.error("Model returned unknown entity_id: %s", entity_id)
        return "That device isn't in the known list — refused for safety. Tell the user you can't act on it."

    if tool == "get_device_status":
        live = await ha_client.get_states([entity_id])
        state = live.get(entity_id, {}).get("state", "unknown")
        return f"{entity_def['name']} is currently: {state}"

    if tool == "control_device":
        domain = inp.get("domain")
        service = inp.get("service")
        if not domain or not service:
            return "control_device needs both a domain and a service."
        service_data = inp.get("service_data") or {}
        duration_minutes = inp.get("duration_minutes")
        description = f"{entity_def['name']}: {service}"

        if entity_def.get("risky"):
            pending_actions.append(make_pending(entity_id, domain, service, service_data, description))
            logger.info("Risky action queued for confirmation: %s", description)
            return (
                "This is a sensitive action and must NOT be treated as done. It is queued and will "
                "run only after the user explicitly confirms. Tell the user what will happen and ask "
                "them to reply 'yes' to confirm — do not say it has been done."
            )

        reply = await _execute_control_action(entity_id, domain, service, service_data, description)
        if duration_minutes and service == "turn_on" and "✅" in reply:
            asyncio.create_task(
                _auto_turn_off_later(sender, entity_id, domain, entity_def["name"], duration_minutes)
            )
            reply += f" (will auto turn-off in {duration_minutes:g} min)"
        return reply

    return f"Unknown tool: {tool}"


async def _broadcast(final_text: str, primary_sender: str, others_only: bool = False) -> None:
    """Sends `final_text` to household senders. If others_only, skips primary (who already got it)."""
    for phone in _allowed_senders():
        if others_only and phone == primary_sender:
            continue
        try:
            await send_message(phone, final_text)
        except Exception:
            logger.exception("Broadcast to %s failed (24h window closed?)", phone)


async def _handle_message(sender: str, text: str, image_block: dict | None = None) -> None:
    confirmed = pop_if_confirmed(sender, text)
    if confirmed is not None:
        replies = []
        for action in confirmed:
            logger.info("Confirmed risky action: %s", action.description)
            replies.append(
                await _execute_control_action(
                    action.entity_id, action.domain, action.service, action.service_data, action.description
                )
            )
        reply = "\n".join(replies)
        await send_message(sender, reply)
        conversation.record(sender, text, reply)
        conversation_log.append(sender, text, reply)
        return

    known_entities = get_known_entities()
    states = await ha_client.get_states(list(known_entities.keys()))

    # Prepend recent turns so follow-ups ("turn it on", "cancel it") have context.
    context_text = initial_context(text, states, sender)
    if image_block is not None:
        first_user_content = [image_block, {"type": "text", "text": context_text}]
    else:
        first_user_content = context_text
    messages: list = conversation.recent(sender) + [
        {"role": "user", "content": first_user_content}
    ]
    pending_actions: list = []
    tools_used: set[str] = set()
    final_text = ""

    for _ in range(_MAX_AGENT_ITERS):
        message = await asyncio.to_thread(run_model, messages)
        tool_uses = [b for b in message.content if b.type == "tool_use"]

        if tool_uses:
            messages.append({"role": "assistant", "content": message.content})
            results = []
            for tu in tool_uses:
                logger.info("Tool call: %s %s", tu.name, tu.input)
                tools_used.add(tu.name)
                result = await _dispatch_tool(sender, tu.name, tu.input, known_entities, pending_actions)
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": result})
            messages.append({"role": "user", "content": results})
            continue

        if message.stop_reason == "pause_turn":
            # A server tool (web search) paused; re-send to let it resume.
            messages.append({"role": "assistant", "content": message.content})
            continue

        final_text = "".join(b.text for b in message.content if b.type == "text").strip()
        break
    else:
        final_text = final_text or "סליחה, זה נהיה מסובך מדי ולא הצלחתי לסיים."

    if pending_actions:
        store_pending(sender, pending_actions)

    # Broadcast expense-related outcomes to the whole household; keep everything else private.
    should_broadcast = bool(tools_used & BROADCAST_TOOLS)

    if final_text:
        await send_message(sender, final_text)
        if should_broadcast:
            await _broadcast(final_text, sender, others_only=True)
        conversation.record(sender, text, final_text)
        conversation_log.append(sender, text, final_text)
    elif not pending_actions:
        fallback = "I'm not sure what you mean — could you rephrase?"
        await send_message(sender, fallback)
        conversation.record(sender, text, fallback)
        conversation_log.append(sender, text, fallback)
