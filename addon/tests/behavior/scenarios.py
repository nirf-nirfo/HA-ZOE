"""Canonical behavior scenarios for the ZOE consistency harness.

Each scenario is a dict with:

- ``name``: stable identifier for pass-rate reporting.
- ``user_message``: what the user would send over WhatsApp.
- ``expected_tool_call``: name of the tool ZOE MUST call. Use ``None`` for
  scenarios that should NOT call any tool (free-form answer).
- ``expected_args_contains`` (optional): dict of arg key -> expected value.
  For scalar values, exact equality (case-insensitive for strings). For a
  dict value, checked as a subset. Use ``__any__`` as the value to just
  assert the key is present.
- ``runs`` (optional, default 3): how many times to run the scenario. Three
  is enough to catch flakiness without exploding cost.
- ``notes`` (optional): human context for what this scenario stresses.

Keep this file additive — new scenarios go at the bottom. Do not renumber
existing ones so historical pass-rate diffs stay meaningful.
"""
from __future__ import annotations

from typing import Any

SCENARIOS: list[dict[str, Any]] = [
    # --- expenses ------------------------------------------------------------
    {
        "name": "add_expense_shorthand",
        "user_message": "150 סופר",
        "expected_tool_call": "add_expense",
        "expected_args_contains": {"amount": 150, "category": "סופר"},
        "notes": "Bare shorthand: amount + Hebrew category, no payment method.",
    },
    {
        "name": "add_expense_with_payment",
        "user_message": "שילמתי 200 בדלק בביט",
        "expected_tool_call": "add_expense",
        "expected_args_contains": {"amount": 200, "category": "דלק", "payment_method": "ביט"},
    },
    {
        "name": "add_expense_max_credit",
        "user_message": "קניתי חולצה ב-200 ב-MAX",
        "expected_tool_call": "add_expense",
        "expected_args_contains": {"amount": 200, "category": "ביגוד", "payment_method": "MAX"},
    },
    {
        "name": "expense_summary_month",
        "user_message": "כמה הוצאנו החודש",
        "expected_tool_call": "expense_summary",
        "expected_args_contains": {"period": "this_month"},
    },
    {
        "name": "expense_summary_category_week",
        "user_message": "כמה על אוכל השבוע",
        "expected_tool_call": "expense_summary",
        "expected_args_contains": {"period": "this_week", "category": "__any__"},
        "notes": "Category may resolve to 'סופר' or 'מסעדות' — either counts.",
    },
    {
        "name": "expense_summary_sender_only",
        "user_message": "כמה אני הוצאתי החודש",
        "expected_tool_call": "expense_summary",
        "expected_args_contains": {"period": "this_month", "sender_only": True},
        "notes": "Pronoun 'אני' should trigger sender_only per the domain rule.",
    },
    # --- reminders -----------------------------------------------------------
    {
        "name": "cancel_reminder_by_text",
        "user_message": "תבטלי את התזכורת על הרופא",
        "expected_tool_call": "delete_reminder_by_text",
        "expected_args_contains": {"text": "__any__"},
    },
    {
        "name": "set_recurring_daily",
        "user_message": "כל יום ב-8 בבוקר להוציא את הכלב",
        "expected_tool_call": "set_reminder",
        "expected_args_contains": {"recurrence": "daily"},
    },
    {
        "name": "set_yearly_birthday",
        "user_message": "יום הולדת לאמא ב-15 באוקטובר",
        "expected_tool_call": "set_reminder",
        "expected_args_contains": {"recurrence": "yearly"},
        "notes": "Yearly reminder — surfaces only in the morning brief.",
    },
    {
        "name": "list_reminders_no_filter",
        "user_message": "מה יש לי בתזכורות",
        "expected_tool_call": "list_reminders",
        "expected_args_contains": {},
        "notes": "Bare 'my reminders' — must NOT pass a kind filter.",
    },
    # --- lists / tasks -------------------------------------------------------
    {
        "name": "add_household_task",
        "user_message": "צריך להחליף בלון גז",
        "expected_tool_call": "add_to_list",
        "expected_args_contains": {"list_name": "tasks"},
    },
    {
        "name": "add_personal_task",
        "user_message": "אני צריך לפתוח באג על התשלומים",
        "expected_tool_call": "add_personal_task",
        "expected_args_contains": {"text": "__any__"},
    },
    {
        "name": "list_shopping",
        "user_message": "מה יש ברשימת קניות",
        "expected_tool_call": "show_list",
        "expected_args_contains": {"list_name": "shopping"},
    },
    {
        "name": "add_to_shopping",
        "user_message": "תוסיפי חלב לרשימת קניות",
        "expected_tool_call": "add_to_list",
        "expected_args_contains": {"list_name": "shopping"},
    },
    # --- anchors / agenda ----------------------------------------------------
    {
        "name": "suppress_anchor_one_off",
        "user_message": "אין כדורגל השישי הקרוב",
        "expected_tool_call": "suppress_anchor_for_date",
        "expected_args_contains": {"text": "__any__", "date": "__any__"},
    },
    {
        "name": "add_agenda_item",
        "user_message": "מחר ב-11 יש לי רופא",
        "expected_tool_call": "add_agenda_item",
        "expected_args_contains": {"date": "__any__", "text": "__any__"},
    },
    {
        "name": "add_weekly_anchor",
        "user_message": "כל שלישי בשמונה בערב יש לי כדורגל",
        "expected_tool_call": "add_anchor",
        "expected_args_contains": {"day": "tuesday"},
    },
    # --- devices -------------------------------------------------------------
    {
        "name": "control_ac_by_room",
        "user_message": "תדליקי מזגן בסלון",
        "expected_tool_call": "control_device",
        "expected_args_contains": {"service": "set_hvac_mode"},
        "notes": "AC turn-on with no explicit mode -> set_hvac_mode=cool.",
    },
    # --- check-ins / memory / briefings --------------------------------------
    {
        "name": "schedule_check_in_hourly",
        "user_message": "כל שעה תבדקי מה סטטוס המשימות שלי",
        "expected_tool_call": "schedule_check_in",
        "expected_args_contains": {"interval_minutes": 60},
    },
    {
        "name": "remember_fact",
        "user_message": "תזכרי שאני צמחוני",
        "expected_tool_call": "remember",
        "expected_args_contains": {"text": "__any__"},
    },
    {
        "name": "set_briefing_time",
        "user_message": "תשלחי לי בריף כל בוקר בשבע",
        "expected_tool_call": "set_daily_briefing",
        "expected_args_contains": {"hour": 7, "minute": 0},
    },
    # --- ambiguity / recent bug patterns -------------------------------------
    {
        "name": "free_form_no_tool",
        "user_message": "מי כתב את המלט",
        "expected_tool_call": None,
        "notes": "Trivia -> plain answer, no tool call.",
    },
    {
        "name": "sub_daily_not_reminder",
        "user_message": "כל חצי שעה תבדקי אם המכונת כביסה נגמרה",
        "expected_tool_call": "schedule_check_in",
        "expected_args_contains": {"interval_minutes": 30},
        "notes": "Sub-daily cadence must NOT become set_reminder.",
    },
    {
        "name": "future_device_action_not_reminder",
        "user_message": "בשעה 12 תדליקי את המזגן בסלון",
        "expected_tool_call": "schedule_action",
        "expected_args_contains": {"run_at": "__any__"},
        "notes": "Future device action must go via schedule_action, not set_reminder.",
    },
]
