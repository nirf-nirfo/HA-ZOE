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


def reset() -> None:
    _CONVERTIBLE.set(None)


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
