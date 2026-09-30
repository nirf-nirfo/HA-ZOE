"""ZOE FastAPI app entrypoint.

After the Item 06 split this file is just:
  - FastAPI app + startup wiring
  - /webhook verify + receive
  - re-exports so tests reaching in via `app.main.X` keep resolving

Everything else lives in dedicated modules:
  - background loops:      app.loops
  - agent tool-use loop:   app.agent_loop
  - handler families:      app.handlers.*
  - health/status routes:  app.status
  - briefing pipeline:     app.briefing_compile
"""
import asyncio
from zoneinfo import ZoneInfo

_IL_TZ = ZoneInfo("Asia/Jerusalem")

from fastapi import FastAPI, Request, Response

# --- re-exports (tests + legacy call-sites reach in via `app.main.X`) -----
from app.claude_agent import (  # noqa: F401
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
from app.confirmation import make_pending, pop_if_confirmed, store_pending  # noqa: F401
from app.briefing_compile import (  # noqa: F401
    _compile_evening_briefing,
    _compile_morning_briefing,
    _gather_day_data,
    _gather_evening_data,
    _gather_morning_data,
    _hebrew_day,
    _day_key,
    _one_line_items_from_dicts as _one_line_items,
    _render_evening_deterministic,
    _render_morning_deterministic,
    _resolve_sender_name,
    _school_note,
    _fmt_ils,
    _HEBREW_DAY_NAMES,
    _DAY_NAMES,
)
from app import (  # noqa: F401
    agenda, anchors, briefing, check_ins, conversation, conversation_log, expenses,
    holidays, inbound_tracker, monitors, personal_tasks, recurring_expenses, scheduled_actions,
    senders,
)
from app import reminders as reminders_mod  # noqa: F401
from app.reminders import (  # noqa: F401
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
from app.ha_client import ha_client  # noqa: F401
from app.lists import add_item, clear_list, get_all_list_names, get_list, remove_items  # noqa: F401
from app.logging_config import logger
from app.memory import all_facts, forget, remember  # noqa: F401
from app.settings import settings
from app.status import (  # noqa: F401
    _LOOP_HEARTBEAT,
    _STARTUP_TS,
    _VERSION,
    _beat,
    _health_payload,
    _reach_cache,
    _require_lan,
    status_router,
)
from app.transcribe import transcribe_audio  # noqa: F401
from app.whatsapp import download_media, extract_message, send_message, verify_signature  # noqa: F401

# Loops + agent loop re-exports (tests and other callers reach in via app.main.X).
from app.agent_loop import (  # noqa: F401
    _MAX_AGENT_ITERS,
    _allowed_senders,
    _broadcast,
    _dispatch_tool,
    _handle_message,
    _prior_check_in_context,
    _process_message,
    _run_check_in,
)
from app.loops import (  # noqa: F401
    _META_WINDOW_HOURS,
    _META_WINDOW_WARNING,
    _WARN_AT_HOURS,
    _INACTIVE_CUTOFF_HOURS,
    _check_in_loop,
    _daily_briefing_loop,
    _meta_window_loop,
    _monitor_loop,
    _reminder_loop,
    _scheduled_action_loop,
    spawn_loops,
)
from app.handlers.agenda import _handle_agenda_call  # noqa: F401
from app.handlers.anchors import _HEBREW_DAY_LOOKUP, _handle_anchor_call  # noqa: F401
from app.handlers.check_ins import _handle_check_in_call  # noqa: F401
from app.handlers.conversation import _handle_conversation_call  # noqa: F401
from app.handlers.expenses import _handle_expense_call  # noqa: F401
from app.handlers.lists import _handle_list_call  # noqa: F401
from app.handlers.memory import _handle_memory_call  # noqa: F401
from app.handlers.monitors import _handle_monitor_call  # noqa: F401
from app.handlers.personal_tasks import _handle_personal_task_call  # noqa: F401
from app.handlers.reminders import _REMINDER_GROUPS, _fmt_reminder, _handle_reminder_call  # noqa: F401
from app.handlers.scheduled_actions import (  # noqa: F401
    _auto_turn_off_later,
    _execute_control_action,
    _handle_scheduled_action_call,
)
from app.handlers._common import _valid_date  # noqa: F401


app = FastAPI(title="ZOE")
app.include_router(status_router)


@app.on_event("startup")
async def startup() -> None:
    fixed = normalize_recurring()
    if fixed:
        logger.info("Self-healed %d yearly reminder(s) to their correct next occurrence", fixed)
    tagged = anchors.auto_tag_school()
    if tagged:
        logger.info("Auto-tagged %d anchor(s) as 'school' from text heuristics", tagged)
    spawn_loops()


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
