"""Item 06 split: handler subpackage.

DISPATCH_TABLE is the single lookup consulted by `agent_loop._dispatch_tool`:
tool name -> handler callable with the uniform signature
    (sender, tool, inp, known_entities, pending_actions) -> str | Awaitable[str]

Sync handlers are wrapped so the dispatcher can `await` unconditionally.
Device handlers stay async because they touch HA over the network.
"""
from typing import Awaitable, Callable

from app.claude_agent import (
    AGENDA_TOOLS,
    ANCHOR_TOOLS,
    CHECK_IN_TOOLS,
    CONVERSATION_TOOLS,
    EXPENSE_TOOLS,
    LIST_TOOLS,
    MEMORY_TOOLS,
    MONITOR_TOOLS,
    PERSONAL_TASK_TOOLS,
    REMINDER_TOOLS,
    SCHEDULED_ACTION_TOOLS,
)
from app.handlers.agenda import _handle_agenda_call
from app.handlers.anchors import _handle_anchor_call
from app.handlers.check_ins import _handle_check_in_call
from app.handlers.conversation import _handle_conversation_call
from app.handlers.devices import _handle_device_call
from app.handlers.expenses import _handle_expense_call
from app.handlers.lists import _handle_list_call
from app.handlers.memory import _handle_memory_call
from app.handlers.monitors import _handle_monitor_call
from app.handlers.personal_tasks import _handle_personal_task_call
from app.handlers.reminders import _handle_reminder_call
from app.handlers.scheduled_actions import _handle_scheduled_action_call


HandlerFn = Callable[..., "str | Awaitable[str]"]


def _wrap_reminder(sender, tool, inp, known_entities, pending_actions):
    return _handle_reminder_call(sender, tool, inp)


def _wrap_list(sender, tool, inp, known_entities, pending_actions):
    return _handle_list_call(sender, tool, inp)


def _wrap_memory(sender, tool, inp, known_entities, pending_actions):
    return _handle_memory_call(tool, inp)


def _wrap_monitor(sender, tool, inp, known_entities, pending_actions):
    return _handle_monitor_call(sender, tool, inp, known_entities)


def _wrap_scheduled_action(sender, tool, inp, known_entities, pending_actions):
    return _handle_scheduled_action_call(sender, tool, inp, known_entities)


def _wrap_agenda(sender, tool, inp, known_entities, pending_actions):
    return _handle_agenda_call(sender, tool, inp)


def _wrap_anchor(sender, tool, inp, known_entities, pending_actions):
    return _handle_anchor_call(tool, inp)


def _wrap_conversation(sender, tool, inp, known_entities, pending_actions):
    return _handle_conversation_call(sender, tool, inp)


def _wrap_expense(sender, tool, inp, known_entities, pending_actions):
    return _handle_expense_call(sender, tool, inp)


def _wrap_check_in(sender, tool, inp, known_entities, pending_actions):
    return _handle_check_in_call(sender, tool, inp)


def _wrap_personal_task(sender, tool, inp, known_entities, pending_actions):
    return _handle_personal_task_call(sender, tool, inp)


async def _wrap_device(sender, tool, inp, known_entities, pending_actions):
    return await _handle_device_call(sender, tool, inp, known_entities, pending_actions)


def _build_dispatch_table() -> dict[str, HandlerFn]:
    """Fan out each tool-family set into a single {tool_name: handler} map so
    dispatch is one dict lookup instead of a chain of `in` tests."""
    table: dict[str, HandlerFn] = {}
    for names, fn in (
        (REMINDER_TOOLS, _wrap_reminder),
        (LIST_TOOLS, _wrap_list),
        (MEMORY_TOOLS, _wrap_memory),
        (MONITOR_TOOLS, _wrap_monitor),
        (SCHEDULED_ACTION_TOOLS, _wrap_scheduled_action),
        (AGENDA_TOOLS, _wrap_agenda),
        (ANCHOR_TOOLS, _wrap_anchor),
        (CONVERSATION_TOOLS, _wrap_conversation),
        (EXPENSE_TOOLS, _wrap_expense),
        (CHECK_IN_TOOLS, _wrap_check_in),
        (PERSONAL_TASK_TOOLS, _wrap_personal_task),
    ):
        for name in names:
            table[name] = fn
    # Device handlers stay off the table; agent_loop routes them explicitly
    # since they need the same fallback ("unknown entity_id") behavior for any
    # tool that isn't in the table.
    return table


DISPATCH_TABLE: dict[str, HandlerFn] = _build_dispatch_table()
