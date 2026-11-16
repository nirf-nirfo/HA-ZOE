"""Handles taps on the list↔expense convert button that Zoe attaches to every
`add_to_list` / `add_expense` reply. Short-circuits the inbound before the LLM
loop: the user asked for an inverse of an action we just took, so the LLM has
nothing useful to add and spending another round-trip on it would be waste.

Button id format (set in `agent_loop._handle_message`):
  revert:list:<list_name>:<item_id>     — list→expense convert
  revert:expense:<expense_id>           — expense→list convert
"""
import re

from app import expenses, lists
from app.handlers._common import _fmt_ils
from app.logging_config import logger

# Default shopping list — matches the canonical name in claude_agent.py so the
# converted item lands in the same bucket the LLM would have used.
_DEFAULT_LIST = "shopping"

# Default category/payment for list→expense conversion. The source message was
# a bare "N item" with no payment context, so we can't infer better than this;
# the user can edit via the normal expense tools afterward.
_DEFAULT_CATEGORY = "סופר"
_DEFAULT_PAYMENT = "לא צוין"


def _parse_amount(text: str) -> tuple[float | None, str]:
    """Pulls the first integer/decimal out of free text; returns (amount, rest).
    Used to turn list text like "3 חלב" or "חלב 3" into an expense amount+desc."""
    match = re.search(r"\d+(?:\.\d+)?", text)
    if not match:
        return None, text.strip()
    amount = float(match.group(0))
    rest = (text[: match.start()] + text[match.end():]).strip()
    rest = re.sub(r"\s+", " ", rest)
    return amount, rest


def _format_amount(amount: float) -> str:
    """Compact amount for list text: "3" not "3.0", "2.5" preserved."""
    if amount == int(amount):
        return str(int(amount))
    return f"{amount:g}"


def handle(sender: str, button_id: str) -> tuple[str, bool] | None:
    """Executes the revert; returns (reply_text, broadcast) or None if the id is
    unparseable (caller should log + ignore). `broadcast=True` means the reply
    should go to the whole household (expense ledger changed)."""
    parts = button_id.split(":", 3)
    if len(parts) < 3 or parts[0] != "revert":
        logger.warning("Unknown button payload: %s", button_id)
        return None
    kind = parts[1]

    if kind == "list":
        if len(parts) != 4:
            logger.warning("Malformed list revert payload: %s", button_id)
            return None
        list_name, item_id = parts[2], parts[3]
        removed = lists.remove_by_id(list_name, item_id)
        if not removed:
            return ("הפריט כבר לא ברשימה — אין מה להמיר.", False)
        amount, description = _parse_amount(removed.text)
        if amount is None:
            # Nothing to use as an amount; put the item back so the user isn't
            # left with a disappeared list entry on a failed convert.
            lists.add_item(list_name, removed.text, sender)
            return (
                f"לא הצלחתי להמיר '{removed.text}' להוצאה — אין מספר בטקסט.",
                False,
            )
        description = description or removed.text
        expenses.add(
            sender,
            amount,
            _DEFAULT_CATEGORY,
            _DEFAULT_PAYMENT,
            description,
            source="manual",
            raw_message=f"[convert] {removed.text}",
        )
        return (
            f"🔄 הועבר להוצאות: {_fmt_ils(amount)} · {_DEFAULT_CATEGORY} — {description}",
            True,
        )

    if kind == "expense":
        if len(parts) != 3:
            logger.warning("Malformed expense revert payload: %s", button_id)
            return None
        expense_id = parts[2]
        removed = expenses.delete_by_id(expense_id)
        if not removed:
            return ("ההוצאה כבר לא קיימת — אין מה להמיר.", False)
        amt = _format_amount(removed.amount)
        text = f"{amt} {removed.description}".strip() if removed.description else amt
        lists.add_item(_DEFAULT_LIST, text, sender)
        return (f"🔄 הועבר לרשימת הקניות: {text}", True)

    logger.warning("Unknown revert kind: %s", kind)
    return None
