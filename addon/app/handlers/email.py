"""Item 19: thin async dispatcher for the three read-only email tools.

The actual work (IMAP backend wiring, "not configured" short-circuit, result
shaping) lives in `app.claude_agent` so the test suite can mock the single
backend instance there in one place. This module exists only to adapt those
dict-returning handlers to the dispatcher's `(sender, tool, inp) -> str`
contract and route the three tool names.
"""

from __future__ import annotations

import json

from app import claude_agent


def _to_int(value, default: int) -> int:
    """Narrow a tool-arg to int, defaulting on missing / unparseable input.

    The Anthropic SDK already validates against the input_schema, but a
    stray string ("10") or None still gets through on some retries — mirror
    the belt-and-braces narrowing _handle_conversation_call does on
    `days_back`.
    """
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


async def _handle_email_call(sender: str, tool: str, inp: dict) -> str:  # noqa: ARG001
    """Returns a JSON-encoded tool result string for one of the email tools.

    sender is accepted for signature compatibility with the dispatcher — email
    access is a household-wide inbox, so the sender's phone doesn't filter
    the results; it only matters for the (private, non-broadcast) reply.
    """
    if tool == claude_agent._LIST_RECENT_EMAILS:
        payload = await claude_agent.handle_list_recent_emails(
            limit=_to_int(inp.get("limit"), 10),
            since_hours=_to_int(inp.get("since_hours"), 24),
        )
    elif tool == claude_agent._READ_EMAIL:
        uid = (inp.get("uid") or "").strip()
        if not uid:
            return json.dumps({"error": "uid is required"})
        payload = await claude_agent.handle_read_email(uid=uid)
    elif tool == claude_agent._SEARCH_EMAILS:
        query = (inp.get("query") or "").strip()
        if not query:
            return json.dumps({"error": "query is required"})
        payload = await claude_agent.handle_search_emails(
            query=query, limit=_to_int(inp.get("limit"), 10)
        )
    else:
        return ""
    # ensure_ascii=False so Hebrew subject lines don't come back as \uXXXX
    # noise the model has to decode.
    return json.dumps(payload, ensure_ascii=False)
