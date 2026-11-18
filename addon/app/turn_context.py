"""Per-turn scratch space populated by tool handlers and consumed by the agent
loop when composing the outbound reply.

Today this carries one thing: the "convertible" record stored whenever Zoe adds
to a list OR logs an expense, so the reply gets wrapped with the
list↔expense convert button. Threaded via a ContextVar so each parallel
`_process_message` task sees its own value without signature changes through
the handler dispatch chain.
"""
from contextvars import ContextVar

_CONVERTIBLE: ContextVar[dict | None] = ContextVar("zoe_convertible", default=None)
# Inbound wamid for the current turn. Used by `react_to_message` to know which
# message to attach the reaction to without plumbing message_id through every
# handler's signature.
_INBOUND_MSG_ID: ContextVar[str | None] = ContextVar("zoe_inbound_msg_id", default=None)
# Flag set when a handler emits a side-effect that IS the reply (today: a
# WhatsApp reaction). When true, _handle_message suppresses the normal
# send_message of final_text so the LLM's text (if it emitted any) doesn't
# duplicate the reaction with a text follow-up.
_REPLY_HANDLED: ContextVar[bool] = ContextVar("zoe_reply_handled", default=False)


def reset() -> None:
    _CONVERTIBLE.set(None)
    _INBOUND_MSG_ID.set(None)
    _REPLY_HANDLED.set(False)


def set_inbound_message_id(message_id: str | None) -> None:
    _INBOUND_MSG_ID.set(message_id)


def get_inbound_message_id() -> str | None:
    return _INBOUND_MSG_ID.get()


def mark_reply_handled() -> None:
    _REPLY_HANDLED.set(True)


def reply_handled() -> bool:
    return _REPLY_HANDLED.get()


def record_list_add(list_name: str, item_id: str, text: str) -> None:
    _CONVERTIBLE.set({
        "kind": "list",
        "list_name": list_name,
        "item_id": item_id,
        "text": text,
    })


def record_expense_add(
    expense_id: str,
    amount: float,
    description: str,
    category: str,
    payment_method: str,
) -> None:
    _CONVERTIBLE.set({
        "kind": "expense",
        "expense_id": expense_id,
        "amount": amount,
        "description": description,
        "category": category,
        "payment_method": payment_method,
    })


def get_convertible() -> dict | None:
    return _CONVERTIBLE.get()
