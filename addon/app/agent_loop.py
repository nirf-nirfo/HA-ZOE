"""Item 06 split: agent tool-use loop + dispatch.

Owns `_handle_message` (the interactive turn), `_run_check_in` (scheduled
check-in turn), `_broadcast`, `_process_message`, and `_dispatch_tool`.
Pure move out of main.py.
"""
import asyncio
import base64
import time
from inspect import isawaitable

from anthropic import APIError

from app import conversation, conversation_log, inbound_tracker, turn_context
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
from app.whatsapp import download_media, send_interactive_message, send_message


# Safety cap on the agentic tool-use loop, so a confused turn can't call tools forever.
_MAX_AGENT_ITERS = 6


def _api_error_user_message(exc: APIError) -> str:
    """Translates an Anthropic APIError into a short WhatsApp line the user
    can act on. Credit exhaustion and auth failures get specific wording so
    the user knows exactly what to fix; everything else gets a generic
    'API error' with the SDK's message appended so it's at least visible."""
    text = str(exc)
    lower = text.lower()
    if "credit balance is too low" in lower or "credit" in lower and "low" in lower:
        return (
            "⚠️ Anthropic credits נגמרו — אני לא יכולה להגיב עד שתחדש. "
            "console.anthropic.com → Plans & Billing."
        )
    if "authentication" in lower or "invalid x-api-key" in lower or "unauthorized" in lower:
        return "⚠️ ה-Anthropic API key לא תקף. תבדוק את ההגדרה של ZOE ב-HA."
    if "rate limit" in lower or "429" in lower:
        return "⚠️ רייט-לימיט של Anthropic. נסה שוב בעוד דקה."
    # Keep the generic surfaced too — silent failure is worse than noisy.
    return f"⚠️ שגיאה מול Anthropic: {text[:200]}"


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


# Patterns the model historically emits when it means "I decided there's
# nothing to send" — we've seen these in production even with explicit
# instructions. Any match is treated as the model opting to stay silent.
_SILENT_META_PATTERNS = (
    "אין מה לשלוח",
    "nothing to send",
    "nothing to report",
    "staying silent",
    "stay silent",
    "no update",
    "אין עדכון",
    "סריקה ללא",
    "לא נמצא",
)


def _is_silent_sentinel(text: str) -> bool:
    """True when the check-in model's final text is really 'stay silent'.

    Covers two shapes:
    1. The explicit `SILENT` sentinel from the framing (tolerant of trailing
       punctuation and surrounding whitespace).
    2. The paren-wrapped meta-commentary failure mode (`(אין מה לשלוח — ...)`,
       `(nothing to send — ...)`, etc.) — if the whole text is wrapped in
       parens AND contains one of the known silent-note phrases, it's a
       narration of the stay-silent decision, not an actual message.
    """
    s = text.strip()
    bare = s.strip("().[]{}!。.").strip().upper()
    if bare in {"SILENT", "SILENTLY", "STAY SILENT", "STAY_SILENT"}:
        return True
    if s.startswith("(") and s.endswith(")"):
        lower = s.lower()
        if any(p in lower for p in _SILENT_META_PATTERNS):
            return True
    return False


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
        "You set up this check-in earlier. Follow this instruction:\n\n"
        f"{ci_prompt}{prior_block}\n\n"
        "Read any state you need via read-only tools, then decide:\n"
        "• If there IS something worth sending to the user right now, reply with "
        "the final WhatsApp message text — it will be sent VERBATIM. Do NOT wrap "
        "it in parentheses, do NOT prefix it with meta-commentary about your "
        "reasoning or your scanning process. Just the message.\n"
        "• If after reading state you decide there is NOTHING worth sending "
        "(no update, nothing new, the condition in the instruction is not met), "
        "reply with EXACTLY the single word SILENT and nothing else — no "
        "parentheses, no explanation. The scheduler will skip the send. Do NOT "
        "compose an 'אין מה לשלוח' / 'nothing to report' / 'staying silent' "
        "message: SILENT is the way to stay silent."
    )
    context = initial_context(framing, states, sender)
    messages: list = [{"role": "user", "content": context}]
    final_text = ""

    try:
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
    except APIError as exc:
        # Check-ins run on the scheduler, not from a user turn — silently
        # dying here is especially bad because the user never knows a scheduled
        # check-in was attempted. Log it and send a visible note instead.
        logger.error("Anthropic API error during check-in %s: %s", check_in_id, exc)
        try:
            await send_message(sender, _api_error_user_message(exc))
        except Exception:
            logger.exception("Could not even notify %s about the API error", sender)
        return

    if not final_text:
        logger.warning("Check-in produced no message for %s (prompt=%r)", sender, ci_prompt)
        return
    if _is_silent_sentinel(final_text):
        logger.info("Check-in %s: model opted to stay silent", check_in_id)
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
    # Clear any convertible record left over in this task's ContextVar from a
    # previous turn (same task gets reused across messages via create_task from
    # the same parent context), so the reply wrapper only sees what THIS turn set.
    turn_context.reset()
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

    try:
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
    except APIError as exc:
        # Previously this exception bubbled up to the asyncio task and died
        # silently — the user saw no reply at all and had to guess why Zoe
        # went quiet. Surface it, and give credit-exhaustion the specific
        # wording that was most painful to miss.
        logger.error("Anthropic API error during _handle_message: %s", exc)
        await send_message(sender, _api_error_user_message(exc))
        return

    if pending_actions:
        store_pending(sender, pending_actions)

    # Broadcast expense-related outcomes to the whole household; keep everything else private.
    should_broadcast = bool(tools_used & BROADCAST_TOOLS)

    if final_text:
        convertible = turn_context.get_convertible()
        # The convert button only makes sense for the shopping↔expense
        # confusion case. A tasks-list add (or any other named list) has no
        # meaningful 'this is an expense' sibling, so skip the button entirely
        # for those and send plain text.
        is_shopping_list = (
            convertible is not None
            and convertible["kind"] == "list"
            and convertible.get("list_name") == "shopping"
        )
        is_expense = convertible is not None and convertible["kind"] == "expense"
        if is_shopping_list or is_expense:
            if is_shopping_list:
                button = {
                    "id": f"revert:list:{convertible['list_name']}:{convertible['item_id']}",
                    "title": "זו הוצאה",
                }
            else:
                button = {
                    "id": f"revert:expense:{convertible['expense_id']}",
                    "title": "זה לרשימת קניות",
                }
            await send_interactive_message(sender, final_text, [button])
        else:
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

    # Button taps short-circuit the LLM: we already know what the user wants
    # (the inverse of an action we just logged), so there's nothing to reason
    # about. Run the revert, record it in conversation history, broadcast if
    # the ledger changed, and return.
    if parsed.button_id:
        from app import revert
        outcome = revert.handle(sender, parsed.button_id)
        if outcome is None:
            return
        reply, broadcast = outcome
        await send_message(sender, reply)
        if broadcast:
            await _broadcast(reply, sender, others_only=True)
        marker = f"[tapped convert button: {parsed.button_id}]"
        conversation.record(sender, marker, reply)
        conversation_log.append(sender, marker, reply)
        return

    text = parsed.text
    image_block = None

    if parsed.audio_id:
        logger.info("Inbound voice from %s, transcribing...", sender)
        # Fire-and-forget ack so the user sees something within a second, well
        # before transcription (ffmpeg + Whisper) completes. Errors here must
        # never block the transcription path — swallow them.
        async def _send_ack() -> None:
            try:
                await send_message(sender, "\U0001F399️ שומעת רגע...")
            except Exception:
                logger.exception("Voice ack to %s failed", sender)
        asyncio.create_task(_send_ack())
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
