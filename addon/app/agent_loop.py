"""Item 06 split: agent tool-use loop + dispatch.

Owns `_handle_message` (the interactive turn), `_run_check_in` (scheduled
check-in turn), `_broadcast`, `_process_message`, and `_dispatch_tool`.
Pure move out of main.py.
"""
import asyncio
import base64
import time
from inspect import isawaitable

from app import conversation, conversation_log, inbound_tracker
from app.claude_agent import (
    BROADCAST_TOOLS,
    get_known_entities,
    initial_context,
    run_check_in_model,
    run_model,
)
from app.confirmation import pop_if_confirmed, store_pending
from app.handlers import DISPATCH_TABLE
from app.handlers.devices import _handle_device_call
from app.ha_client import ha_client
from app.logging_config import logger
from app.settings import settings
from app.transcribe import transcribe_audio
from app.whatsapp import download_media, send_message


# Safety cap on the agentic tool-use loop, so a confused turn can't call tools forever.
_MAX_AGENT_ITERS = 6


def _allowed_senders() -> set[str]:
    return {n.strip() for n in settings.allowed_sender_numbers.split(",") if n.strip()}


async def _dispatch_tool(
    sender: str, tool: str, inp: dict, known_entities: dict, pending_actions: list
) -> str:
    """Executes one tool call and returns a result string fed back to the model.
    Risky device actions are not executed here — they're queued for user confirmation."""
    handler = DISPATCH_TABLE.get(tool)
    if handler is not None:
        result = handler(sender, tool, inp, known_entities, pending_actions)
        if isawaitable(result):
            return await result
        return result
    # Not a store/family tool -> device tool (control_device / get_device_status)
    # or unknown; device handler owns the "unknown entity_id" refusal path.
    return await _handle_device_call(sender, tool, inp, known_entities, pending_actions)


async def _broadcast(final_text: str, primary_sender: str, others_only: bool = False) -> None:
    """Sends `final_text` to household senders. If others_only, skips primary (who already got it)."""
    for phone in _allowed_senders():
        if others_only and phone == primary_sender:
            continue
        try:
            await send_message(phone, final_text)
        except Exception:
            logger.exception("Broadcast to %s failed (24h window closed?)", phone)


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
    from app import check_ins  # local to keep import graph acyclic on rare edges
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


async def _handle_message(sender: str, text: str, image_block: dict | None = None) -> None:
    from app.handlers.scheduled_actions import _execute_control_action
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
