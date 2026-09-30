"""Item 06 split: scheduled-action tool handlers + shared execution helpers.

`_execute_control_action` and `_auto_turn_off_later` live here because the
scheduled_action loop and the devices handler both need them; keeping them
in one module avoids a cross-handler import chain.
"""
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from app import scheduled_actions
from app.ha_client import ha_client
from app.logging_config import logger
from app.whatsapp import send_message

_IL_TZ = ZoneInfo("Asia/Jerusalem")


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
