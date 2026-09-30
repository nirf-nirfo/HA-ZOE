"""Broadcast semantics: only expense MUTATIONS should broadcast to household.
Read-only expense queries stay private to the sender (M2 review finding)."""
from app.claude_agent import BROADCAST_TOOLS


def test_expense_summary_does_not_broadcast():
    """Regression for review finding M2: read-only queries (expense_summary,
    list_recent_expenses) must NOT be in BROADCAST_TOOLS or every 'how much
    did we spend?' pings the spouse's WhatsApp."""
    assert "expense_summary" not in BROADCAST_TOOLS
    assert "list_recent_expenses" not in BROADCAST_TOOLS


def test_expense_mutations_broadcast():
    """Mutations MUST broadcast so both partners see spending as it happens."""
    for name in (
        "add_expense",
        "delete_last_expense",
        "fix_last_expense",
        "add_recurring_expense",
        "remove_recurring_expense",
    ):
        assert name in BROADCAST_TOOLS, f"{name} should broadcast"


def test_non_expense_tools_do_not_broadcast():
    """Device control, reminders, tasks — none should broadcast (private to sender)."""
    for name in (
        "control_device",
        "set_reminder",
        "add_agenda_item",
        "add_personal_task",
        "add_to_list",
        "remember",
        "schedule_check_in",
    ):
        assert name not in BROADCAST_TOOLS, f"{name} must NOT broadcast"
