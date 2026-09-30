"""Item 06 split: past-conversation search tool handler."""
from datetime import datetime
from zoneinfo import ZoneInfo

from app import conversation_log

_IL_TZ = ZoneInfo("Asia/Jerusalem")


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
