"""Handler for the react_to_message tool.

Zoe picks this for pure ack-only inbounds ('תודה', 'אוקיי', 'קיבלתי') instead
of composing a trivial text reply. The reaction IS the response, so this
handler also marks `turn_context.reply_handled` so agent_loop's post-loop
sender skips the normal send of the LLM's text (if the LLM emitted any).

The handler needs the inbound wamid to attach the reaction to. It's not in
the handler signature, so we pull it from turn_context (set at the start of
every _handle_message turn).
"""
from __future__ import annotations

from app import turn_context
from app.whatsapp import send_reaction


async def _handle_reaction_call(sender: str, tool: str, inp: dict) -> str:
    if tool != "react_to_message":
        return ""
    emoji = (inp.get("emoji") or "").strip()
    if not emoji:
        # Fall through to a plain text reply by not marking reply_handled,
        # so the LLM's final_text (if any) still goes out.
        return "I need an emoji to react with."
    message_id = turn_context.get_inbound_message_id()
    if not message_id:
        # No inbound wamid in context — this happens for scheduled check-ins
        # or revert-button handlers, which have nothing meaningful to react to.
        # Don't silently swallow; tell the LLM so it falls back to a text reply.
        return "No inbound message is available to react to right now; reply with text instead."
    await send_reaction(sender, message_id, emoji)
    turn_context.mark_reply_handled()
    # Tool result goes back to the LLM. Return something terse so the LLM
    # doesn't feel compelled to compose a text wrap-up.
    return f"Reacted with {emoji}. The user already sees this; no text reply needed."
