from datetime import datetime
from zoneinfo import ZoneInfo

_IL_TZ = ZoneInfo("Asia/Jerusalem")
from pathlib import Path
from typing import Any

import yaml
from anthropic import Anthropic

from app.memory import all_facts
from app.settings import settings

_client = Anthropic(api_key=settings.anthropic_api_key)

_CONTROL_TOOL = "control_device"
_STATUS_TOOL = "get_device_status"
_SET_REMINDER = "set_reminder"
_LIST_REMINDERS = "list_reminders"
_DELETE_REMINDER = "delete_reminder"
_DELETE_REMINDER_BY_TEXT = "delete_reminder_by_text"
_RESCHEDULE_REMINDER = "reschedule_reminder"
_DELETE_ALL_REMINDERS = "delete_all_reminders"

_ADD_TO_LIST = "add_to_list"
_REMOVE_FROM_LIST = "remove_from_list"
_CLEAR_LIST = "clear_list"
_SHOW_LIST = "show_list"
_SHOW_ALL_LISTS = "show_all_lists"

_REMEMBER = "remember"
_FORGET = "forget"

_MONITOR_DEVICE = "monitor_device"
_LIST_MONITORS = "list_monitors"
_CANCEL_MONITOR = "cancel_monitor"

_SCHEDULE_ACTION = "schedule_action"
_LIST_SCHEDULED_ACTIONS = "list_scheduled_actions"
_CANCEL_SCHEDULED_ACTION = "cancel_scheduled_action"

_ADD_AGENDA_ITEM = "add_agenda_item"
_LIST_AGENDA = "list_agenda"
_REMOVE_AGENDA_ITEM = "remove_agenda_item"
_SET_DAILY_BRIEFING = "set_daily_briefing"
_SET_EVENING_BRIEFING = "set_evening_briefing"

_ADD_ANCHOR = "add_anchor"
_LIST_ANCHORS = "list_anchors"
_REMOVE_ANCHOR = "remove_anchor"
_SUPPRESS_ANCHOR = "suppress_anchor_for_date"

_SEARCH_CONVERSATIONS = "search_past_conversations"

_SCHEDULE_CHECK_IN = "schedule_check_in"
_LIST_CHECK_INS = "list_check_ins"
_CANCEL_CHECK_IN = "cancel_check_in"

_ADD_EXPENSE = "add_expense"
_DELETE_LAST_EXPENSE = "delete_last_expense"
_FIX_LAST_EXPENSE = "fix_last_expense"
_LIST_RECENT_EXPENSES = "list_recent_expenses"
_EXPENSE_SUMMARY = "expense_summary"
_ADD_RECURRING_EXPENSE = "add_recurring_expense"
_LIST_RECURRING_EXPENSES = "list_recurring_expenses"
_REMOVE_RECURRING_EXPENSE = "remove_recurring_expense"

REMINDER_TOOLS = {
    _SET_REMINDER,
    _LIST_REMINDERS,
    _DELETE_REMINDER,
    _DELETE_REMINDER_BY_TEXT,
    _RESCHEDULE_REMINDER,
    _DELETE_ALL_REMINDERS,
}
LIST_TOOLS = {_ADD_TO_LIST, _REMOVE_FROM_LIST, _CLEAR_LIST, _SHOW_LIST, _SHOW_ALL_LISTS}
MEMORY_TOOLS = {_REMEMBER, _FORGET}
MONITOR_TOOLS = {_MONITOR_DEVICE, _LIST_MONITORS, _CANCEL_MONITOR}
SCHEDULED_ACTION_TOOLS = {_SCHEDULE_ACTION, _LIST_SCHEDULED_ACTIONS, _CANCEL_SCHEDULED_ACTION}
AGENDA_TOOLS = {_ADD_AGENDA_ITEM, _LIST_AGENDA, _REMOVE_AGENDA_ITEM, _SET_DAILY_BRIEFING, _SET_EVENING_BRIEFING}
ANCHOR_TOOLS = {_ADD_ANCHOR, _LIST_ANCHORS, _REMOVE_ANCHOR, _SUPPRESS_ANCHOR}
CONVERSATION_TOOLS = {_SEARCH_CONVERSATIONS}
CHECK_IN_TOOLS = {_SCHEDULE_CHECK_IN, _LIST_CHECK_INS, _CANCEL_CHECK_IN}

# Tools ZOE may call from inside a scheduled check-in — read-only + write to
# conversation log only. Prevents a check-in from silently controlling devices,
# logging expenses, or scheduling more check-ins while the user isn't there.
CHECK_IN_ALLOWED_TOOLS = {
    _STATUS_TOOL, _LIST_REMINDERS, _LIST_AGENDA, _LIST_ANCHORS, _LIST_MONITORS,
    _LIST_SCHEDULED_ACTIONS, _LIST_RECURRING_EXPENSES, _LIST_RECENT_EXPENSES,
    _EXPENSE_SUMMARY, _SHOW_LIST, _SHOW_ALL_LISTS, _SEARCH_CONVERSATIONS,
}
EXPENSE_TOOLS = {
    _ADD_EXPENSE, _DELETE_LAST_EXPENSE, _FIX_LAST_EXPENSE, _LIST_RECENT_EXPENSES, _EXPENSE_SUMMARY,
    _ADD_RECURRING_EXPENSE, _LIST_RECURRING_EXPENSES, _REMOVE_RECURRING_EXPENSE,
}
# Tools whose successful use should broadcast the reply to all household senders,
# not just the one who sent the request. Everything else stays private to the sender.
BROADCAST_TOOLS = {
    _ADD_EXPENSE, _DELETE_LAST_EXPENSE, _FIX_LAST_EXPENSE, _EXPENSE_SUMMARY,
    _ADD_RECURRING_EXPENSE, _REMOVE_RECURRING_EXPENSE,
}

SYSTEM_PROMPT = (
    "You are ZOE, a personal assistant reachable over WhatsApp that also controls "
    "Home Assistant. You are given a list of known smart-home devices (entities) with "
    "their current state. "
    "When the user asks you to do something to one of those devices, call the "
    "control_device tool with the exact entity_id, domain, and service from the device "
    "list. "
    "When the user asks about the current state of a device instead of asking you to "
    "change it, call the get_device_status tool with that entity_id instead. "
    "If the user's message implies acting on more than one device (e.g. \"close both "
    "shutters\"), call control_device once per device, in the same turn. "
    "Only act on devices in the list — never invent an entity_id. "
    "If an entity with domain=automation matches what the user is asking for (by name or "
    "clear intent, e.g. a routine like \"morning\" or \"leaving the house\"), call "
    "control_device on that automation with service=trigger, and do NOT also separately "
    "control other individual devices yourself — the automation already does whatever it "
    "is configured to do. Only control individual devices directly when no matching "
    "automation exists for what the user asked. "
    "For a cover entity, to set a specific open percentage use service=set_cover_position "
    "with service_data={\"position\": <0-100>}, where 0 is fully closed and 100 is fully open. "
    "If the user asks to turn something on for a specific duration (e.g. \"turn on the "
    "boiler for an hour\"), call control_device with service=turn_on and also set "
    "duration_minutes to that many minutes — ZOE will turn it back off automatically when "
    "the time is up. Only set duration_minutes together with turn_on. "
    "When the user asks to be reminded about something, call set_reminder with the reminder "
    "text and the exact ISO 8601 datetime (e.g. 2026-07-03T09:00:00). Use the current "
    "datetime provided in the context to resolve relative times like 'tomorrow', 'in 2 hours', "
    "'next Sunday'. All times are in Israel time (Asia/Jerusalem). "
    "CRITICAL: set_reminder.text is delivered VERBATIM to the user as a WhatsApp message at that "
    "time — write it as the exact short human message the user will read on their phone ('לא לשכוח "
    "לתת גלולה לכלב', 'להתקשר לאמא'). NEVER write it as a description of what YOU (ZOE) will do at "
    "that moment ('לקרוא את הרשימה ולשאול את המשתמש...'), and NEVER as an internal plan or checklist. "
    "If the user actually wants you to READ fresh state (lists, agenda, expenses, device status) at "
    "that time and send a dynamically composed message — e.g. 'each evening check my tasks list and "
    "ask about status', 'every Friday remind me about weekend plans using the agenda' — do NOT use "
    "set_reminder for that; use schedule_check_in instead (see below). "
    "When the user gives a calendar date without a year (e.g. '8th of January'), always pick "
    "the next occurrence of that date in the future — if it has already passed this year, use "
    "next year. send_at must never be in the past. "
    "For a repeating reminder, set the recurrence field (daily/weekly/monthly/yearly) — for "
    "example a birthday every year on the same date is recurrence='yearly', and take daily "
    "medication is recurrence='daily'. send_at is the first occurrence; ZOE reschedules the rest "
    "automatically, so create just ONE reminder, not one per date. Omit recurrence for a one-off. "
    "When the user asks to see their reminders, call list_reminders. If they ask for only a "
    "certain kind — e.g. 'my doctor appointments' or 'the one-off reminders' or 'non-recurring' "
    "(kind='one_time'), or 'my birthdays'/'the yearly ones' (kind='yearly'), etc. — pass the "
    "matching kind so the recurring ones don't bury the one-time ones. Omit kind to show all. "
    "When you show reminders to the user, ALWAYS keep each one's full date INCLUDING THE YEAR and "
    "its time exactly as the tool returned them — never drop the year or the time. The year "
    "matters: a yearly birthday may be scheduled for next year if this year's date already passed. "
    "When the user cancels a specific reminder by describing it (not by id), call "
    "delete_reminder_by_text with a snippet of its text — no need to look up the id first. "
    "Use delete_reminder with an id only when you already have the exact id. "
    "When the user wants to move a reminder to a different time, call reschedule_reminder with a "
    "snippet of its text and the new time. If the move is relative to its current time (e.g. "
    "'a day earlier', 'push it two hours'), call list_reminders first to read the current time, "
    "then compute the new absolute time and pass that. "
    "When the user asks to delete or cancel ALL reminders, call delete_all_reminders. "
    "For shared lists: use add_to_list to add an item, remove_from_list to remove an item by "
    "its text, clear_list to wipe the whole list, show_list to display one list, and "
    "show_all_lists to see the names of all existing lists. "
    "The user can have any number of lists on any topic — use whatever list name the user "
    "names (e.g. 'ספרים', 'סרטים לראות', 'מתנות', 'packing'). Derive list_name from the user's "
    "own wording and keep it consistent for that same list across messages. "
    "Two names are canonical so the common lists don't fragment: use list_name='shopping' for "
    "grocery/shopping lists ('רשימת קניות', 'shopping list'), and list_name='tasks' for "
    "to-do/task lists ('משימות', 'tasks', 'to-do'). For every other topic, use the user's name for it. "
    "If the user refers to a list whose exact name you are unsure of, call show_all_lists first "
    "to see what exists rather than guessing or creating a near-duplicate. "
    "Lists are shared between all family members. "
    "For air conditioners (climate domain): call set_hvac_mode with service_data "
    "{\"hvac_mode\": ...} where the mode is one of cool, heat, dry, fan_only, auto, or off "
    "('קור'/'קירור'=cool, 'חום'/'חימום'=heat, 'יבש'=dry, 'מאוורר'=fan_only). Turning an AC on "
    "for cooling is set_hvac_mode='cool'; turning it off is set_hvac_mode='off'. Set the "
    "temperature with set_temperature and service_data {\"temperature\": N} in Celsius (16-30). "
    "Set fan speed with set_fan_mode. If the user just says 'turn on the AC' without a mode, "
    "use cool. "
    "When the user asks you to keep an eye on a device over time and notify them if it's in a "
    "wrong state — e.g. 'check every hour for 3 days that the front door is locked, and tell me "
    "if it's not' — call monitor_device: entity_id, expected_state (the state it SHOULD be in, "
    "exactly as it appears in the device list, e.g. 'locked'), interval_minutes (how often), until "
    "(ISO datetime derived from the duration, e.g. now + 3 days), and alert_text (what to message "
    "when it's not in the expected state). ZOE checks on that cadence and alerts once each time the "
    "device leaves the expected state. Use list_monitors to show active monitors and cancel_monitor "
    "to stop one. This is different from a reminder (a monitor watches a device's live state); use "
    "a reminder for a plain timed message. "
    "When the user wants a device action to actually HAPPEN at a future time — not just be "
    "reminded about it — call schedule_action, e.g. 'turn on the AC at 12', 'open the shutter at "
    "sunrise', 'unlock the door in an hour'. This really executes the action at that time; do NOT "
    "use set_reminder for this and do NOT just tell the user to message you again then — schedule_action "
    "does it for them automatically. Use list_scheduled_actions to show pending ones and "
    "cancel_scheduled_action to cancel one. A risky device (e.g. the lock) still asks for 'yes' "
    "confirmation at the moment it's due to run, exactly like an immediate risky action would — tell "
    "the user this when scheduling one. "
    "ZOE sends TWO daily briefings: a morning briefing (full agenda for today) and an evening "
    "briefing (short recap of today + a 1-2 line preview of tomorrow). Both are compiled from the "
    "same sources: weekly anchors for that day-of-week, yearly reminders (birthdays) whose date "
    "falls on that day, Jewish/Israeli holidays for that date (with school-vacation status), and "
    "one-off agenda items for that date. "
    "When the user wants to feed you information ahead of time for a specific day (e.g. 'tomorrow I "
    "have a 9am meeting and a dentist at 5', 'on the 20th I have the conference'), call add_agenda_item "
    "with date (ISO YYYY-MM-DD) and text — this is for ONE-OFF things on a specific date. Use "
    "list_agenda to show what's on a date (default today) and remove_agenda_item to remove one. "
    "For RECURRING weekly items — the family's regular schedule anchors, e.g. 'on Sundays Mili finishes "
    "school at 13:00', 'every Tuesday I have soccer at 20:00', 'Mika finishes at 14:00 on Mondays' — "
    "call add_anchor with day (sunday/monday/tuesday/wednesday/thursday/friday/saturday), text, and an "
    "optional time (HH:MM, 24-hour Israel time). Anchors are household-wide (shared across all senders). "
    "Use list_anchors to show all anchors. Call remove_anchor to delete an anchor permanently (by id or "
    "text snippet). When the user says an anchor doesn't apply on ONE specific date ('no soccer this "
    "Sunday', 'Mili has no חוג next Tuesday') — call suppress_anchor_for_date to cancel just that one "
    "occurrence, keeping the weekly anchor otherwise intact. To REPLACE an anchor for a date with "
    "something different, suppress the anchor and add an agenda item for that date. "
    "To change the daily briefing times: call set_daily_briefing (morning) or set_evening_briefing "
    "(evening) with hour, minute, and enabled. Both fire automatically each day at their configured "
    "time. Note: yearly reminders (like birthdays) are surfaced in the morning briefing on their date "
    "instead of firing as standalone messages — do not tell the user a yearly reminder will ping them "
    "at 9am; it will appear in the morning briefing. "
    "For anything that is not about a known device, reminder, or list — general questions, writing or "
    "drafting text, current events, weather, or any other normal personal-assistant "
    "request — do not call any tool. Just answer directly and naturally in plain text, "
    "the same way you would in a normal conversation. Use the web_search tool when you "
    "need current or real-world information you would otherwise be unsure about. "
    "You are told which WhatsApp number each message is from. If a Known fact links that number "
    "to a person and their gender, address them accordingly — in Hebrew use the correct gendered "
    "forms (masculine for a male, feminine for a female). If you don't know the sender's gender, "
    "stay neutral. "
    "You have a long-term memory of durable facts about the user and household — shown to you "
    "each turn under 'Known facts'. Use them naturally without being asked (e.g. if you know the "
    "salon AC is preferred at 23°, use that when they say 'turn on the AC in the salon'). When the "
    "user tells you a lasting preference, name, routine, or fact worth keeping ('we're vegetarian', "
    "'my wife is Dana', 'I like the blinds at 50%'), call remember to save it. Do NOT remember "
    "one-off or transient things (a single shopping item, a specific reminder) — those have their "
    "own tools. When a saved fact becomes wrong or the user asks you to forget it, call forget. "
    "ZOE tracks household expenses (parity with the family's expense bot). All expenses share one "
    "household pot; each row is attributed to the sender who reported it. When the user reports "
    "spending money — e.g. '150 סופר', 'שילמתי 50 שקל בדלק בביט', 'קניתי חולצה ב-200 ב-MAX', or a "
    "photo of a receipt — call add_expense with amount (in ₪), category (from the fixed list: סופר / "
    "מסעדות / דלק / חינוך / בריאות / ביגוד / בית / בילויים / תחבורה / חשבונות / ביטוחים / אחר — pick "
    "the closest match, use 'אחר' only when truly none fit), payment_method (from: MAX / לאומי / "
    "PayBox / בינלאומי / מזומן / ביט / לא צוין — use 'לא צוין' if the user didn't say), and a short "
    "description (what was bought). Also pass `date` in ISO YYYY-MM-DD only when the user is reporting "
    "an expense from a specific past day; otherwise omit and it defaults to today. "
    "When a photo of a receipt arrives, extract the total amount from the receipt and call add_expense. "
    "If there are visibly separate purchases on the same receipt, use the grand total unless the user "
    "asks to split. Non-receipt photos: describe/answer normally without calling add_expense. "
    "Other expense commands: delete_last_expense removes the sender's most recent manual expense; "
    "fix_last_expense updates its amount; list_recent_expenses shows the family's recent spending "
    "(default 10, all household — pass sender_only=true to filter to the current sender). "
    "expense_summary aggregates over a period ('today', 'this_week', 'this_month', 'last_month', "
    "'this_year', or explicit start/end YYYY-MM-DD) and returns totals by sender / category / payment "
    "method. Use it for questions like 'כמה הוצאנו החודש', 'כמה על אוכל השבוע', 'סיכום'. When you "
    "present a summary in Hebrew, translate sender phone numbers to names using the Known facts. "
    "For recurring bills (rent, subscriptions, utilities): add_recurring_expense with name, amount, "
    "day_of_month (1-31), month_pattern (default 'monthly'; for yearly or specific months use "
    "comma-separated English month abbrevs like 'jan' or 'jan,jul'), category, and payment_method. "
    "list_recurring_expenses / remove_recurring_expense manage them. ZOE inserts the actual expense "
    "row automatically on each due day — the user does not need to log it manually. "
    "ZOE keeps a searchable log of every past conversation with each user for up to 90 days. The last "
    "24 hours of exchanges are ALREADY visible to you in this thread (the messages prepended to the "
    "conversation); anything older lives only in the log. When the user references something you "
    "discussed before that you can't see in the current thread — 'what did I tell you about X?', "
    "'remind me about the article last week', 'the plan we made for Y' — call "
    "search_past_conversations with a distinctive keyword. Only call it when the referenced context "
    "isn't in what you can already see; don't search for things obviously in this thread. "
    "You work in a tool-use loop: after you call a tool you will be shown its result, and you "
    "may call more tools before answering. Chain steps when a task needs it — e.g. call "
    "get_device_status, read the result, then decide whether to act; or call list_reminders to "
    "read a reminder's exact time before rescheduling it. When you have finished what the user "
    "asked, reply with one short, natural confirmation of what you did — do not paste raw tool "
    "output, entity_ids, or internal ✅ strings verbatim; phrase it for a person. "
    "Reply in whatever language the user wrote in."
)

MODEL = "claude-opus-5"
MAX_TOKENS = 2048


def _load_entities() -> list[dict[str, Any]]:
    path = Path(settings.entities_config_path)
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data["entities"]


def get_known_entities() -> dict[str, dict[str, Any]]:
    return {e["entity_id"]: e for e in _load_entities()}


def _build_tools(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entity_ids = [e["entity_id"] for e in entities]
    services = sorted({s for e in entities for s in e["services"]})
    return [
        {
            "name": _CONTROL_TOOL,
            "description": "Calls a Home Assistant service on a known entity.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "entity_id": {"type": "string", "enum": entity_ids},
                    "domain": {"type": "string"},
                    "service": {"type": "string", "enum": services},
                    "service_data": {
                        "type": "object",
                        "description": "Optional extra service parameters (e.g. position).",
                    },
                    "duration_minutes": {
                        "type": "number",
                        "description": "If set with service=turn_on, automatically turn the "
                        "device back off after this many minutes.",
                    },
                },
                "required": ["entity_id", "domain", "service"],
            },
        },
        {
            "name": _SCHEDULE_ACTION,
            "description": "Schedules a real Home Assistant action to run automatically at a future "
            "time — unlike a reminder, which only sends a text message, this actually executes the "
            "action. Use when the user wants something DONE at a time, e.g. 'turn on the AC at 12'.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "entity_id": {"type": "string", "enum": entity_ids},
                    "domain": {"type": "string"},
                    "service": {"type": "string", "enum": services},
                    "service_data": {
                        "type": "object",
                        "description": "Optional extra service parameters (e.g. temperature, position).",
                    },
                    "duration_minutes": {
                        "type": "number",
                        "description": "If set with service=turn_on, automatically turn the "
                        "device back off after this many minutes, once it runs.",
                    },
                    "run_at": {
                        "type": "string",
                        "description": "ISO 8601 datetime when to run this (Israel time), e.g. "
                        "2026-08-03T12:00:00. Resolve relative times ('at noon', 'in an hour') "
                        "using the current datetime provided in the context.",
                    },
                },
                "required": ["entity_id", "domain", "service", "run_at"],
            },
        },
        {
            "name": _LIST_SCHEDULED_ACTIONS,
            "description": "Lists the user's pending scheduled actions (device commands set to run "
            "automatically at a future time).",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": _CANCEL_SCHEDULED_ACTION,
            "description": "Cancels a scheduled action, identified by a snippet of the device name "
            "or description it refers to (or its id).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Device/description snippet or id."},
                },
                "required": ["text"],
            },
        },
        {
            "name": _ADD_AGENDA_ITEM,
            "description": "Adds an item to a specific day's agenda, to be read out in ZOE's daily "
            "morning briefing for that date. Use for information given in advance about a day.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "ISO date (YYYY-MM-DD) this item is for, resolved from the "
                        "user's wording relative to the current date.",
                    },
                    "text": {"type": "string", "description": "The agenda item text."},
                },
                "required": ["date", "text"],
            },
        },
        {
            "name": _LIST_AGENDA,
            "description": "Shows agenda items for a given date. Defaults to today if no date given.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "ISO date (YYYY-MM-DD). Optional."},
                },
            },
        },
        {
            "name": _REMOVE_AGENDA_ITEM,
            "description": "Removes an agenda item from a specific date, matched by a text snippet.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "ISO date (YYYY-MM-DD) of the item."},
                    "text": {"type": "string", "description": "Snippet of the item's text to match."},
                },
                "required": ["date", "text"],
            },
        },
        {
            "name": _SET_DAILY_BRIEFING,
            "description": "Configures ZOE's morning briefing: what local time to send it "
            "each morning, and whether it's on. Once set, it's sent automatically every day.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "hour": {"type": "integer", "description": "Hour (0-23, Israel time) to send it."},
                    "minute": {"type": "integer", "description": "Minute (0-59)."},
                    "enabled": {
                        "type": "boolean",
                        "description": "True to turn the morning briefing on (default), false to turn it off.",
                    },
                },
                "required": ["hour", "minute"],
            },
        },
        {
            "name": _SET_EVENING_BRIEFING,
            "description": "Configures ZOE's evening briefing (default 20:00): short today-recap + "
            "1-2 line preview of tomorrow. Set the local time and whether it's on.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "hour": {"type": "integer", "description": "Hour (0-23, Israel time)."},
                    "minute": {"type": "integer", "description": "Minute (0-59)."},
                    "enabled": {"type": "boolean", "description": "True to turn it on, false to turn it off."},
                },
                "required": ["hour", "minute"],
            },
        },
        {
            "name": _ADD_ANCHOR,
            "description": "Adds a weekly recurring 'anchor': a household schedule item keyed by "
            "day-of-week (e.g. 'on Sundays Mili finishes school at 13:00'). Household-wide.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "day": {
                        "type": "string",
                        "enum": ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"],
                        "description": "Day of the week this anchor applies to.",
                    },
                    "text": {"type": "string", "description": "What happens (e.g. 'מילי מסיימת בית ספר')."},
                    "time": {
                        "type": "string",
                        "description": "Optional 24-hour HH:MM (Israel time) when it happens. Omit if no specific time.",
                    },
                },
                "required": ["day", "text"],
            },
        },
        {
            "name": _LIST_ANCHORS,
            "description": "Lists all weekly anchors, grouped by day of the week.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": _REMOVE_ANCHOR,
            "description": "Permanently removes a weekly anchor, identified by a snippet of its text or its id.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Anchor text snippet or id."},
                },
                "required": ["text"],
            },
        },
        {
            "name": _SUPPRESS_ANCHOR,
            "description": "Cancels a single occurrence of a weekly anchor on one specific date, "
            "without removing the weekly template. Use for 'no soccer this Sunday' type overrides.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Anchor text snippet or id."},
                    "date": {"type": "string", "description": "ISO date YYYY-MM-DD to suppress on."},
                },
                "required": ["text", "date"],
            },
        },
        {
            "name": _STATUS_TOOL,
            "description": "Reports the current state of a known entity, without changing it.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "entity_id": {"type": "string", "enum": entity_ids},
                },
                "required": ["entity_id"],
            },
        },
        {
            "name": _SET_REMINDER,
            "description": "Saves a reminder that ZOE will send to the user at the specified time.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The reminder message to send."},
                    "send_at": {
                        "type": "string",
                        "description": "ISO 8601 datetime of the first (or only) time to send the reminder, "
                        "e.g. 2026-07-03T09:00:00.",
                    },
                    "recurrence": {
                        "type": "string",
                        "enum": ["daily", "weekly", "monthly", "yearly"],
                        "description": "Optional. Omit for a one-time reminder. If set, the reminder "
                        "repeats forever at this interval starting from send_at (e.g. 'yearly' for a "
                        "birthday, 'daily' for a daily medication).",
                    },
                },
                "required": ["text", "send_at"],
            },
        },
        {
            "name": _LIST_REMINDERS,
            "description": "Returns the user's pending reminders. By default returns all of them, "
            "grouped by repeat interval. Pass kind to show only one group.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["one_time", "daily", "weekly", "monthly", "yearly"],
                        "description": "Optional filter. 'one_time' = only non-recurring reminders "
                        "(e.g. doctor appointments, one-off errands); daily/weekly/monthly/yearly = "
                        "only reminders that repeat at that interval. Omit to show everything.",
                    },
                },
            },
        },
        {
            "name": _DELETE_REMINDER,
            "description": "Deletes a pending reminder by its id.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "The reminder id to delete."},
                },
                "required": ["id"],
            },
        },
        {
            "name": _DELETE_REMINDER_BY_TEXT,
            "description": "Cancels a reminder identified by a snippet of its text (or its id), "
            "without needing to know the id first. Use when the user cancels by description, e.g. "
            "'cancel the reminder about signing up for soccer'.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "A distinctive snippet of the reminder's text to match, or its id.",
                    },
                },
                "required": ["text"],
            },
        },
        {
            "name": _RESCHEDULE_REMINDER,
            "description": "Moves an existing reminder to a new time, identified by a snippet of its "
            "text (or its id). Use for 'move Saturday's reminder', 'push the doctor reminder to 9am', "
            "etc. For a move relative to the current time (e.g. '24 hours earlier'), first call "
            "list_reminders to read the current time, then compute and pass the new absolute time.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "A distinctive snippet of the reminder's text to match, or its id.",
                    },
                    "send_at": {
                        "type": "string",
                        "description": "The new ISO 8601 datetime, e.g. 2026-08-01T09:00:00 (Israel time).",
                    },
                },
                "required": ["text", "send_at"],
            },
        },
        {
            "name": _DELETE_ALL_REMINDERS,
            "description": "Deletes ALL pending reminders for the user at once.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": _SCHEDULE_CHECK_IN,
            "description": "Schedules a 'check-in': at the given time, ZOE wakes up, reads whatever fresh "
            "state your `prompt` tells her to read (lists, agenda, expenses, device status), and sends a "
            "dynamically composed WhatsApp message to the user. Distinct from set_reminder (static text) "
            "and from schedule_action (device command). Use for 'every evening at 18:00 check my task list "
            "and ask what's done', 'every Friday at 14 remind me about the weekend using the agenda', etc. "
            "The `prompt` field is an instruction to yourself for the moment of firing — write it as: "
            "'Read <list>, then send the user a message that <what to say>'.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Short instruction to yourself for when the check-in fires. State "
                        "WHAT to read (e.g. 'read the tasks list') and WHAT MESSAGE to send the user (e.g. "
                        "'ask which items are done'). Written in the second person to future-you.",
                    },
                    "when": {
                        "type": "string",
                        "description": "ISO 8601 datetime for the first (or only) fire, Israel time. Must be in the future.",
                    },
                    "recurrence": {
                        "type": "string",
                        "enum": ["daily", "weekly", "monthly", "yearly"],
                        "description": "Optional. Omit for a one-time check-in.",
                    },
                },
                "required": ["prompt", "when"],
            },
        },
        {
            "name": _LIST_CHECK_INS,
            "description": "Lists the user's pending check-ins with their next fire time and prompt.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": _CANCEL_CHECK_IN,
            "description": "Cancels a scheduled check-in, matched by id or a snippet of its prompt.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Check-in id or a snippet of its prompt."},
                },
                "required": ["text"],
            },
        },
        {
            "name": _ADD_TO_LIST,
            "description": "Adds an item to a named shared list (e.g. 'shopping', 'tasks').",
            "input_schema": {
                "type": "object",
                "properties": {
                    "list_name": {"type": "string", "description": "Name of the list, e.g. 'shopping' or 'tasks'."},
                    "text": {"type": "string", "description": "The item text to add."},
                },
                "required": ["list_name", "text"],
            },
        },
        {
            "name": _REMOVE_FROM_LIST,
            "description": "Removes items matching the given text from a named list (case-insensitive substring match).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "list_name": {"type": "string"},
                    "text": {"type": "string", "description": "Text to match against items. All matching items are removed."},
                },
                "required": ["list_name", "text"],
            },
        },
        {
            "name": _CLEAR_LIST,
            "description": "Removes all items from a named list.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "list_name": {"type": "string"},
                },
                "required": ["list_name"],
            },
        },
        {
            "name": _SHOW_LIST,
            "description": "Shows all current items in a named list.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "list_name": {"type": "string"},
                },
                "required": ["list_name"],
            },
        },
        {
            "name": _SHOW_ALL_LISTS,
            "description": "Lists the names of all existing lists and how many items each has. "
            "Use when the user asks what lists they have (e.g. 'what lists do I have?', 'איזה רשימות יש לי?').",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": _REMEMBER,
            "description": "Saves a durable fact about the user or household to long-term memory "
            "(a preference, name, routine, or standing fact). Not for one-off items or reminders.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The fact to remember, as a short standalone sentence, "
                        "e.g. 'The salon AC is preferred at 23 degrees.'",
                    },
                },
                "required": ["text"],
            },
        },
        {
            "name": _FORGET,
            "description": "Removes a previously remembered fact, matched by a snippet of its text.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "A snippet of the fact to forget."},
                },
                "required": ["text"],
            },
        },
        {
            "name": _MONITOR_DEVICE,
            "description": "Repeatedly checks one device on a schedule until an end time, and "
            "messages the user whenever the device is NOT in the expected state. Use for requests "
            "like 'check every hour for 3 days that the door is locked and tell me if it isn't'.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "entity_id": {"type": "string", "enum": entity_ids},
                    "expected_state": {
                        "type": "string",
                        "description": "The state the device SHOULD be in, exactly as Home Assistant "
                        "reports it (e.g. 'locked', 'closed', 'off', 'on'). An alert is sent whenever "
                        "the actual state differs from this.",
                    },
                    "alert_text": {
                        "type": "string",
                        "description": "The message to send the user when the device is not in the "
                        "expected state, e.g. 'The front door is not locked!'.",
                    },
                    "interval_minutes": {
                        "type": "number",
                        "description": "How often to check, in minutes (e.g. 60 for hourly).",
                    },
                    "until": {
                        "type": "string",
                        "description": "ISO 8601 datetime when to stop monitoring (Israel time). "
                        "Derive it from the duration, e.g. 'for 3 days' = now + 3 days.",
                    },
                },
                "required": ["entity_id", "expected_state", "alert_text", "interval_minutes", "until"],
            },
        },
        {
            "name": _LIST_MONITORS,
            "description": "Lists the user's active device monitors.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": _CANCEL_MONITOR,
            "description": "Stops a device monitor, identified by a snippet of the device name it "
            "watches (or its id).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Device-name snippet or monitor id."},
                },
                "required": ["text"],
            },
        },
        {
            "name": _ADD_EXPENSE,
            "description": "Records a household expense (ILS). Household-wide; attributed to the sender.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "amount": {"type": "number", "description": "Amount in ₪ (positive number)."},
                    "category": {
                        "type": "string",
                        "enum": [
                            "סופר", "מסעדות", "דלק", "חינוך", "בריאות", "ביגוד",
                            "בית", "בילויים", "תחבורה", "חשבונות", "ביטוחים", "אחר",
                        ],
                    },
                    "payment_method": {
                        "type": "string",
                        "enum": ["MAX", "לאומי", "PayBox", "בינלאומי", "מזומן", "ביט", "לא צוין"],
                    },
                    "description": {"type": "string", "description": "Short description of what was bought."},
                    "date": {
                        "type": "string",
                        "description": "Optional ISO YYYY-MM-DD. Omit for today. Use only when the user is "
                        "reporting an expense from a specific past day.",
                    },
                },
                "required": ["amount", "category", "payment_method", "description"],
            },
        },
        {
            "name": _DELETE_LAST_EXPENSE,
            "description": "Deletes the sender's most recent manual (or receipt) expense — used when they "
            "say 'תמחק', 'ביטול', 'תמחק את האחרון' after just logging something.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": _FIX_LAST_EXPENSE,
            "description": "Updates the amount on the sender's most recent manual expense. Use when the "
            "user says 'תתקן ל-250', 'שנה ל-300' after just logging something.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "new_amount": {"type": "number", "description": "Corrected amount in ₪."},
                },
                "required": ["new_amount"],
            },
        },
        {
            "name": _LIST_RECENT_EXPENSES,
            "description": "Shows recent expenses. Household-wide by default; pass sender_only=true to "
            "filter to just this sender's expenses.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "How many to show (default 10, max 50)."},
                    "sender_only": {"type": "boolean", "description": "True to filter to this sender only."},
                },
            },
        },
        {
            "name": _EXPENSE_SUMMARY,
            "description": "Aggregates household expenses over a period. Returns totals by sender, by "
            "category, by payment method, and the grand total. Use for 'כמה הוצאנו', 'סיכום החודש', "
            "'כמה על אוכל השבוע' etc.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "period": {
                        "type": "string",
                        "enum": ["today", "this_week", "this_month", "last_month", "this_year"],
                        "description": "Preset period. Omit and pass start+end for a custom range.",
                    },
                    "start": {"type": "string", "description": "Custom-range start (ISO YYYY-MM-DD, inclusive)."},
                    "end": {"type": "string", "description": "Custom-range end (ISO YYYY-MM-DD, inclusive)."},
                    "category": {
                        "type": "string",
                        "description": "Optional: filter to one category from the fixed list.",
                    },
                    "sender_only": {
                        "type": "boolean",
                        "description": "True to include only the current sender's expenses in the totals.",
                    },
                },
            },
        },
        {
            "name": _ADD_RECURRING_EXPENSE,
            "description": "Adds a recurring monthly (or yearly / specific-months) household bill. ZOE "
            "auto-inserts the actual expense row each due day.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Bill name, e.g. 'ארנונה', 'נטפליקס'."},
                    "amount": {"type": "number"},
                    "day_of_month": {"type": "integer", "description": "1-31. Clamped to month-end for short months."},
                    "month_pattern": {
                        "type": "string",
                        "description": "Default 'monthly'. For yearly / specific months, comma-separated "
                        "English abbrevs like 'jan' (yearly on Jan) or 'jan,jul' (bi-annual).",
                    },
                    "category": {
                        "type": "string",
                        "enum": [
                            "סופר", "מסעדות", "דלק", "חינוך", "בריאות", "ביגוד",
                            "בית", "בילויים", "תחבורה", "חשבונות", "ביטוחים", "אחר",
                        ],
                        "description": "Default 'חשבונות'.",
                    },
                    "payment_method": {
                        "type": "string",
                        "enum": ["MAX", "לאומי", "PayBox", "בינלאומי", "מזומן", "ביט", "לא צוין"],
                    },
                },
                "required": ["name", "amount", "day_of_month"],
            },
        },
        {
            "name": _LIST_RECURRING_EXPENSES,
            "description": "Lists all recurring household bills with their day and pattern.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": _REMOVE_RECURRING_EXPENSE,
            "description": "Removes a recurring bill by id or by a snippet of its name.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Recurring bill id or a name snippet."},
                },
                "required": ["text"],
            },
        },
        {
            "name": _SEARCH_CONVERSATIONS,
            "description": "Searches this sender's past conversations with ZOE for a keyword or phrase. "
            "Returns matching exchanges (both what the user said and what ZOE replied) with dates, "
            "newest first. Use when the user references something you discussed earlier that isn't in "
            "the current thread anymore — e.g. 'what did I tell you about X?', 'the article from last "
            "week', 'remind me what we said about the trip'. Retention is 90 days.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "A distinctive word or short phrase to search for.",
                    },
                    "days_back": {
                        "type": "integer",
                        "description": "Optional: only look at exchanges from the last N days. "
                        "Omit to search all available history.",
                    },
                },
                "required": ["query"],
            },
        },
        {"type": "web_search_20250305", "name": "web_search", "max_uses": 3},
    ]


def initial_context(user_text: str, states: dict[str, Any], sender: str | None = None) -> str:
    """Builds the first user message: current time, sender, device catalog + live state, request."""
    entities = _load_entities()
    entity_summary = "\n".join(
        f"- {e['entity_id']} ({e['name']}): domain={e['domain']}, "
        f"services={e['services']}, state={states.get(e['entity_id'], {}).get('state', 'unknown')}"
        for e in entities
    )
    now_str = datetime.now(_IL_TZ).strftime("%Y-%m-%dT%H:%M:%S")
    facts = all_facts()
    facts_block = (
        "Known facts (long-term memory):\n" + "\n".join(f"- {f.text}" for f in facts) + "\n\n"
        if facts
        else ""
    )
    sender_line = f"Message is from WhatsApp number: {sender}\n" if sender else ""
    return (
        f"Current datetime (Israel): {now_str}\n"
        f"{sender_line}"
        f"Known devices:\n{entity_summary}\n\n"
        f"{facts_block}"
        f"User message: {user_text}"
    )


CHECK_IN_SYSTEM_SUFFIX = (
    "\n\nYou are being invoked as a SCHEDULED CHECK-IN. The user did not just message you — you set "
    "up this check-in earlier to fire now. Read whatever fresh state the check-in prompt asks for "
    "(via the read-only tools available), then produce ONE clear, natural WhatsApp message to send "
    "to the user right now. Do NOT prefix it with '⏰' or '[check-in]' — write it as if you decided "
    "to text them. You cannot control devices, log expenses, schedule anything new, or modify state "
    "from a check-in — only read and reply."
)


def run_model(messages: list[dict[str, Any]]) -> Any:
    """One turn of the agentic loop: sends the running transcript and returns the raw
    Anthropic message (content blocks + stop_reason). The caller executes any tool_use
    blocks, appends the results, and calls again until stop_reason is not tool_use."""
    tools = _build_tools(_load_entities())
    return _client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        tools=tools,
        messages=messages,
    )


def run_check_in_model(messages: list[dict[str, Any]]) -> Any:
    """One turn of a check-in agent loop, restricted to read-only tools."""
    all_tools = _build_tools(_load_entities())
    tools = [
        t for t in all_tools
        if t.get("name") in CHECK_IN_ALLOWED_TOOLS or t.get("type") == "web_search_20250305"
    ]
    return _client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT + CHECK_IN_SYSTEM_SUFFIX,
        tools=tools,
        messages=messages,
    )
