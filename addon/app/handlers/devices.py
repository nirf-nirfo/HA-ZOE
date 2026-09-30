"""Item 06 split: control_device + get_device_status handlers.

These are the async handlers; every other tool family stays synchronous.
Risky control_device tools defer to confirmation.py's pending queue rather
than actuating immediately — same behavior as before the split.
"""
import asyncio

from app.confirmation import make_pending
from app.ha_client import ha_client
from app.handlers.scheduled_actions import _auto_turn_off_later, _execute_control_action
from app.logging_config import logger


async def _handle_device_call(
    sender: str, tool: str, inp: dict, known_entities: dict, pending_actions: list
) -> str:
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
