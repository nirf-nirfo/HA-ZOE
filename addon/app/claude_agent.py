import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

_IL_TZ = ZoneInfo("Asia/Jerusalem")
from pathlib import Path
from typing import Any

import yaml
from anthropic import Anthropic

from app.memory import all_facts
from app.settings import settings

logger = logging.getLogger(__name__)

_client = Anthropic(api_key=settings.anthropic_api_key)


def _log_usage(label: str, model: str, usage: Any) -> None:
    """Log model + input / cache_read / cache_write / output token counts for a turn.

    Emitted only when the SDK actually returns cache-related counts, so pre-
    caching baseline turns stay quiet. Lets us eyeball cache hit rate — and,
    with Item 04's hybrid routing, which model actually answered — in the add-on
    logs without a dedicated metrics endpoint.
    """
    if usage is None:
        return
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    if not (cache_read or cache_write):
        return
    logger.info(
        "%s tokens: model=%s input=%d cache_read=%d cache_write=%d output=%d",
        label,
        model,
        getattr(usage, "input_tokens", 0) or 0,
        cache_read,
        cache_write,
        getattr(usage, "output_tokens", 0) or 0,
    )

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

_ADD_PERSONAL_TASK = "add_personal_task"
_LIST_PERSONAL_TASKS = "list_personal_tasks"
_COMPLETE_PERSONAL_TASK = "complete_personal_task"
_CLEAR_PERSONAL_TASKS = "clear_personal_tasks"

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
PERSONAL_TASK_TOOLS = {_ADD_PERSONAL_TASK, _LIST_PERSONAL_TASKS, _COMPLETE_PERSONAL_TASK, _CLEAR_PERSONAL_TASKS}

# Tools ZOE may call from inside a scheduled check-in — read-only + write to
# conversation log only. Prevents a check-in from silently controlling devices,
# logging expenses, or scheduling more check-ins while the user isn't there.
CHECK_IN_ALLOWED_TOOLS = {
    _STATUS_TOOL, _LIST_REMINDERS, _LIST_AGENDA, _LIST_ANCHORS, _LIST_MONITORS,
    _LIST_SCHEDULED_ACTIONS, _LIST_RECURRING_EXPENSES, _LIST_RECENT_EXPENSES,
    _EXPENSE_SUMMARY, _SHOW_LIST, _SHOW_ALL_LISTS, _SEARCH_CONVERSATIONS,
    _LIST_PERSONAL_TASKS,
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

# The system prompt is split into named sections so the stable parts can be
# marked with Anthropic's cache_control (see run_model / run_check_in_model).
# Content is intentionally identical to the pre-split single-string version —
# any reword/reorder is Item 17's job, not this refactor.
PERSONA = (
    "You are ZOE (זואי), a personal assistant reachable over WhatsApp that also controls "
    "Home Assistant. You are given a list of known smart-home devices (entities) with "
    "their current state. "
    "Your own identity: your name is ZOE (זואי), you are a female-gendered assistant. "
    "When you refer to yourself in Hebrew, use FEMININE forms — 'אני שמחה', 'אני יכולה', "
    "'שלחתי לך', 'רשמתי לפניי'. This applies to every self-reference regardless of the "
    "user's gender. When asked 'מי את' / 'איך קוראים לך', answer that your name is Zoe (זואי). "
)

TOOL_POLICY = (
    "## Precedence when rules conflict\n"
    "\n"
    "When two rules seem to conflict, apply in this order:\n"
    "1. Safety — risky-device confirmation, LAN-only endpoints, never invent an entity_id.\n"
    "2. Broadcast policy — expense outcomes broadcast; everything else private.\n"
    "3. Specific tool guidance in the section for that tool.\n"
    "4. General style / tone.\n"
    "\n"
    "## Devices\n"
    "\n"
    "### control_device\n"
    "What: calls a Home Assistant service on a known entity. "
    "When: user asks you to change or trigger a device — 'turn on the AC', 'close the shutter', "
    "'run the morning routine'. Use the exact entity_id, domain, and service from the device list. "
    "If the message implies acting on more than one device ('close both shutters'), call once per "
    "device in the same turn. "
    "When NOT: for a state-only question ('is the door locked?') — use get_device_status. For a "
    "future execution ('at noon turn on the AC') — use schedule_action. "
    "Edge cases: "
    "(1) An entity with domain=automation matching the request by name or clear intent (e.g. "
    "'morning', 'leaving the house') → call control_device on that automation with "
    "service=trigger, and do NOT also separately control the individual devices — the automation "
    "already does whatever it is configured to do. Only touch individual devices directly when "
    "no matching automation exists. "
    "(2) For a cover, set a specific open percentage via service=set_cover_position with "
    "service_data={\"position\": <0-100>} (0=fully closed, 100=fully open). "
    "(3) For a timed-off ('turn on the boiler for an hour'), pass service=turn_on and also "
    "duration_minutes; ZOE turns it back off automatically. Only set duration_minutes with turn_on. "
    "(4) Air conditioners (climate domain): use set_hvac_mode with service_data "
    "{\"hvac_mode\": <cool|heat|dry|fan_only|auto|off>} ('קור'/'קירור'=cool, 'חום'/'חימום'=heat, "
    "'יבש'=dry, 'מאוורר'=fan_only). 'Turn on the AC' with no mode → cool. 'Turn off the AC' → "
    "set_hvac_mode='off'. Set temperature via set_temperature with service_data "
    "{\"temperature\": N} in Celsius (16-30). Fan speed via set_fan_mode. "
    "\n\n"
    "### get_device_status\n"
    "What: reports the current state of one entity without changing it. "
    "When: user asks about state — 'is the AC on?', 'is the door locked?'. Also chain it BEFORE a "
    "control_device call when the right action depends on the current state. "
    "When NOT: when the user wants to change the state — go straight to control_device. "
    "\n\n"
    "### schedule_action / list_scheduled_actions / cancel_scheduled_action\n"
    "What: schedule_action queues a real HA action to run at a future time — this actually "
    "executes; it is not a reminder message. "
    "When: 'turn on the AC at 12', 'open the shutter at sunrise', 'unlock the door in an hour'. "
    "When NOT: do NOT use set_reminder for a future device action, and do NOT tell the user to "
    "message you again then. schedule_action does it for them automatically. "
    "Edge cases: a risky device (e.g. the lock) still asks for 'yes' confirmation at the moment "
    "it's due to run, exactly like an immediate risky action — tell the user this when scheduling. "
    "Use list_scheduled_actions to show pending ones and cancel_scheduled_action to remove one. "
    "\n\n"
    "### monitor_device / list_monitors / cancel_monitor\n"
    "What: repeatedly checks one device on a schedule until an end time; messages the user "
    "whenever the device is NOT in the expected state. "
    "When: 'check every hour for 3 days that the front door is locked, and tell me if it isn't'. "
    "Pass entity_id, expected_state (exactly as HA reports it, e.g. 'locked'), interval_minutes, "
    "until (ISO datetime derived from the duration), and alert_text (the message when the state "
    "diverges). "
    "When NOT: for a plain timed message use set_reminder — a monitor watches live device state, "
    "not the clock. "
    "\n\n"
    "## Reminders (per-sender)\n"
    "\n"
    "### set_reminder\n"
    "What: saves a WhatsApp message that ZOE sends to this sender at send_at. "
    "When: 'remind me to call mom tomorrow at 9', 'take pill daily at 8'. Pass text and an ISO "
    "8601 send_at; resolve relative times ('tomorrow', 'in 2 hours', 'next Sunday') against the "
    "current datetime in the context. For repeats, set recurrence (daily/weekly/monthly/yearly): "
    "send_at is the first occurrence; ZOE reschedules the rest automatically — create just ONE "
    "reminder, not one per date. Omit recurrence for a one-off. "
    "When NOT: (a) DYNAMIC composition — 'each evening check my tasks and ask about status', "
    "'every Friday remind me about weekend plans using the agenda' — use schedule_check_in. "
    "(b) FUTURE DEVICE ACTION — use schedule_action. (c) SUB-DAILY cadence ('every hour', 'every "
    "30 min') — use schedule_check_in with interval_minutes; recurrence has no 'hourly' value. "
    "CRITICAL for text: it is delivered VERBATIM as the user's WhatsApp message. Write it as the "
    "short human message they will read ('לא לשכוח לתת גלולה לכלב', 'להתקשר לאמא'). NEVER phrase "
    "it as what YOU (ZOE) will do at that moment ('לקרוא את הרשימה ולשאול את המשתמש...'), and "
    "NEVER as an internal plan or checklist. "
    "\n\n"
    "### list_reminders\n"
    "What: returns pending reminders, grouped by kind. "
    "When: user asks 'what reminders do I have?'. If they narrow to a kind ('my doctor "
    "appointments' / 'the one-off ones' / 'non-recurring' → kind='one_time'; 'my birthdays' / "
    "'the yearly ones' → kind='yearly'; etc.), pass that kind so the recurring group doesn't bury "
    "the one-times. "
    "When NOT: for a bare 'my reminders', OMIT kind — show everything. "
    "Edge case: when you render reminders to the user, ALWAYS keep each date INCLUDING THE YEAR "
    "and each time exactly as the tool returned them — never drop the year or time. A yearly "
    "birthday may be scheduled for next year if this year's date already passed. "
    "\n\n"
    "### delete_reminder_by_text / delete_reminder / delete_all_reminders / reschedule_reminder\n"
    "What: delete_reminder_by_text cancels a reminder identified by a snippet of its text; "
    "delete_reminder takes an id; delete_all_reminders wipes them all; reschedule_reminder moves "
    "one to a new time. "
    "When: user cancels by description — 'תבטלי את התזכורת על הרופא' → delete_reminder_by_text "
    "with the snippet, no need to look up the id first. Use delete_reminder only when you already "
    "have the exact id. 'Cancel all my reminders' → delete_all_reminders. Move a reminder → "
    "reschedule_reminder with a snippet + the new absolute time. "
    "Edge case for reschedule: if the move is relative to the reminder's CURRENT time ('a day "
    "earlier', 'push it two hours'), first call list_reminders to read the current time, then "
    "compute the new absolute time and pass that. "
    "\n\n"
    "## Check-ins and personal tasks (per-sender)\n"
    "\n"
    "### schedule_check_in / list_check_ins / cancel_check_in\n"
    "What: at fire time ZOE wakes up, reads whatever fresh state your `prompt` tells her to read "
    "(via read-only tools), and sends a dynamically composed WhatsApp message to the user. "
    "When: (a) DYNAMIC RECURRING message — 'every evening at 18:00 check my task list and ask "
    "what's done', 'every Friday at 14 remind me about the weekend using the agenda'. (b) "
    "SUB-DAILY / WORK-SESSION cadence — user wants a periodic ping about progress: first call "
    "add_personal_task once per thing they said they're working on, then schedule_check_in with "
    "prompt='Read my open personal tasks and ask about progress on each', when=now+interval, "
    "interval_minutes=<cadence>. "
    "When NOT: for a static verbatim reminder message → set_reminder. For a scheduled device "
    "action → schedule_action. "
    "Edge cases: 'stop asking' / 'מספיק' / 'תעצרי' → cancel_check_in on that check-in. When the "
    "user reports a task done during a work-session cadence, also call complete_personal_task so "
    "the next fire doesn't ask about it again. `prompt` is written in the second person to "
    "future-you: WHAT to read + WHAT MESSAGE to send. list_check_ins shows pending ones. "
    "\n\n"
    "### add_personal_task / list_personal_tasks / complete_personal_task / clear_personal_tasks\n"
    "What: the sender's PRIVATE to-do list — never visible to other family members. "
    "When: 'I need to open a bug on X', 'email the boss', 'review PR from Dan'. "
    "When NOT: for a shared household chore that anyone might do — use add_to_list with "
    "list_name='tasks'. See the HOUSEHOLD vs PER-SENDER rule in the domain block for the "
    "language-cue heuristics and the ambiguity-→-ask rule. "
    "\n\n"
    "## Agenda, anchors and briefings\n"
    "\n"
    "ZOE sends TWO daily briefings: a morning briefing (full agenda for today) and an evening "
    "briefing (short recap of today + a 1-2 line preview of tomorrow). Both are compiled from "
    "the same sources: weekly anchors for that day-of-week, yearly reminders (birthdays) whose "
    "date falls on that day, Jewish/Israeli holidays for that date (with school-vacation status), "
    "and one-off agenda items for that date. "
    "\n\n"
    "### add_agenda_item / list_agenda / remove_agenda_item\n"
    "What: pins a piece of info to a specific calendar date; surfaced in that day's morning brief. "
    "When: user feeds info in advance for a specific day — 'tomorrow I have a 9am meeting and a "
    "dentist at 5', 'on the 20th I have the conference'. Pass date (ISO YYYY-MM-DD) and text. "
    "When NOT: for a weekly-recurring family-schedule item → add_anchor. For a WhatsApp message "
    "at a specific time → set_reminder. "
    "\n\n"
    "### add_anchor / list_anchors / remove_anchor / suppress_anchor_for_date\n"
    "What: weekly recurring household schedule keyed by day-of-week; household-wide. "
    "When: 'on Sundays Mili finishes school at 13:00', 'every Tuesday I have soccer at 20:00'. "
    "Pass day (sunday..saturday), text, and optional 24-hour HH:MM. "
    "When the anchor is a school-related time (school start, school end, school pickup), also "
    "pass tags=['school']. ZOE uses this to automatically suppress the anchor from morning "
    "briefings on Jewish-holiday days when the school is closed (חול המועד, first day of "
    "Sukkot, etc.). Do the same for other categorizable anchors when it would help future "
    "filtering — for now 'school' is the only one the briefing actually uses. "
    "Cancelling one occurrence only: 'no soccer this Sunday', 'Mili has no חוג next Tuesday' → "
    "suppress_anchor_for_date, keeping the weekly template intact. To REPLACE an anchor for a "
    "date with something different, suppress the anchor and add_agenda_item for that date. "
    "Removing permanently: remove_anchor. "
    "\n\n"
    "### set_daily_briefing / set_evening_briefing\n"
    "What: configures the send time (and on/off) for the morning / evening brief. "
    "When: 'send me the brief every morning at 7', 'move the evening brief to 21:30'. "
    "(See the YEARLY REMINDERS rule in the domain block for how yearly reminders surface — do "
    "not promise a 09:00 ping for them.) "
    "\n\n"
    "## Household lists\n"
    "\n"
    "### add_to_list / remove_from_list / clear_list / show_list / show_all_lists\n"
    "What: shared named lists — 'shopping', 'tasks', or any user-named topic. All lists are "
    "visible to and mutated by every family member. "
    "When: user says 'add milk to shopping', 'what's on the shopping list', 'clear the packing "
    "list'. The user can have any number of lists on any topic — derive list_name from their "
    "wording (e.g. 'ספרים', 'סרטים לראות', 'מתנות', 'packing') and keep it consistent across "
    "messages. "
    "Canonical names (do not fragment): list_name='shopping' for grocery/shopping lists ('רשימת "
    "קניות', 'shopping list'), and list_name='tasks' for household to-do lists ('משימות', "
    "'tasks', 'to-do'). "
    "When NOT: for a private per-sender to-do → add_personal_task. See the HOUSEHOLD vs "
    "PER-SENDER rule in the domain block for the household/personal split. "
    "Edge case: if the user refers to a list whose exact name you are unsure of, call "
    "show_all_lists first to see what exists rather than guessing or creating a near-duplicate. "
    "\n\n"
    "## Memory\n"
    "\n"
    "### remember / forget\n"
    "What: durable facts about the user and household — shown to you each turn under 'Known "
    "facts'. Use them naturally without being asked (e.g. if you know the salon AC is preferred "
    "at 23°, use that when they say 'turn on the AC in the salon'). "
    "When: user tells you a lasting preference, name, routine, or fact worth keeping — 'we're "
    "vegetarian', 'my wife is Dana', 'I like the blinds at 50%' → call remember. When a saved "
    "fact becomes wrong or the user asks you to forget it → forget with a snippet. "
    "When NOT: do NOT remember one-off or transient things (a single shopping item, a specific "
    "reminder) — those have their own tools. "
    "\n\n"
    "## Expenses (household, broadcast)\n"
    "\n"
    "All expenses share one household pot; each row is attributed to the sender who reported it. "
    "The tool outcomes in this section broadcast to both senders (see BROADCAST SEMANTICS in the "
    "domain block). See ENUM HEURISTICS for category/payment picks and RECEIPT IMAGES for how to "
    "handle photos. "
    "\n\n"
    "### add_expense\n"
    "What: records one household expense in ILS. "
    "When: user reports spending money — '150 סופר', 'שילמתי 50 שקל בדלק בביט', 'קניתי חולצה "
    "ב-200 ב-MAX', or a photo of a receipt. Pass amount (₪), category, payment_method, and a "
    "short description of what was bought. Pass `date` (ISO YYYY-MM-DD) ONLY when the user is "
    "reporting a specific past day; otherwise omit and it defaults to today. "
    "When NOT: for a bank-notification screenshot or a non-receipt image → answer normally "
    "without add_expense (see RECEIPT IMAGES domain rule). "
    "\n\n"
    "### delete_last_expense / fix_last_expense / list_recent_expenses / expense_summary\n"
    "What: delete_last_expense removes the sender's most recent expense; fix_last_expense updates "
    "its amount; list_recent_expenses shows recent spending (default 10, household-wide; pass "
    "sender_only=true for just this sender); expense_summary aggregates over a period. "
    "When: 'תמחק' / 'ביטול' / 'תמחק את האחרון' just after logging → delete_last_expense. "
    "'תתקן ל-250' / 'שנה ל-300' just after logging → fix_last_expense. 'כמה הוצאנו החודש' / "
    "'כמה על אוכל השבוע' / 'סיכום' → expense_summary with the matching period ('today', "
    "'this_week', 'this_month', 'last_month', 'this_year', or explicit start+end YYYY-MM-DD) and "
    "optional category filter. "
    "Edge case: pronoun ambiguity — 'כמה הוצאתי' / 'how much did I spend' is per-sender; 'כמה "
    "הוצאנו' / 'how much did we spend' is household-wide. Pass sender_only=true for the personal "
    "reading; omit for the household reading. "
    "\n\n"
    "### add_recurring_expense / list_recurring_expenses / remove_recurring_expense\n"
    "What: recurring bill templates (rent, subscriptions, utilities). ZOE inserts the actual "
    "expense row automatically on each due day — the user does not log it manually. "
    "When: 'add ארנונה 800 שח בכל 5 לחודש', 'add Netflix 55 shekel monthly'. Pass name, amount, "
    "day_of_month (1-31), month_pattern (default 'monthly'; for yearly / specific months use "
    "comma-separated English abbrevs like 'jan' or 'jan,jul'), category, and payment_method. "
    "\n\n"
    "## Past-conversation search\n"
    "\n"
    "### search_past_conversations\n"
    "What: searches this sender's exchanges with ZOE for a keyword; returns matching exchanges "
    "(user + ZOE) with dates, newest first. Retention 90 days. "
    "When: user references something you discussed before that isn't in the current thread — "
    "'what did I tell you about X?', 'remind me about the article last week', 'the plan we made "
    "for Y'. "
    "When NOT: the last 24h of exchanges are ALREADY visible to you in this thread — don't "
    "search for things obviously in front of you. "
    "\n\n"
    "## Free-form answers and web search\n"
    "\n"
    "For anything not about a known device, reminder, list, expense, agenda, anchor, task, "
    "check-in or memory — general questions, drafting text, current events, weather, or any "
    "other normal personal-assistant request — do NOT call any tool. Just answer directly in "
    "plain text, the same way you would in a normal conversation. Use the web_search tool when "
    "you need current or real-world information you would otherwise be unsure about. "
)

# Domain rules — how ZOE's world works, independent of any specific tool. These
# were previously scattered across TOOL_POLICY (Item 02 left the block empty
# with a note that Item 17 would fill it). Consolidated here so the model reads
# each domain fact once, and the tool blocks below can focus on the tool
# contracts themselves. Cached separately as part of the third cache block.
DOMAIN_RULES = (
    "## Domain rules\n"
    "\n"
    "TIME. All datetimes are Israel local time (Asia/Jerusalem). "
    "When the user gives a date without a year (e.g. '8th of January'), always pick "
    "the next occurrence of that date in the future — if it has already passed this "
    "year, use next year. Times you write into tool calls (send_at, when, run_at, "
    "until, date) must never be in the past. When a dated reminder has no time-of-day, "
    "default to 09:00 Israel time. "
    "\n\n"
    "LANGUAGE. Reply in whatever language the user wrote in. In Hebrew, use "
    "sender-aware gendered forms based on the Known facts (masculine 'אתה' for a "
    "male sender, feminine 'את' for a female sender). If you don't know the "
    "sender's gender, stay neutral (plural / infinitive constructions). "
    "\n\n"
    "SENDER RESOLUTION. Each turn tells you which WhatsApp number the message is "
    "from. Known facts often link that number to a person's name. Whenever you "
    "render a phone number in a reply — expense summaries, household lists, "
    "broadcasts — translate it to that name. If you don't have a fact linking the "
    "number to a name, fall back to the number itself. "
    "\n\n"
    "HOUSEHOLD vs PER-SENDER. Household-wide stores (visible to and mutated by any "
    "family member): anchors, lists, memory, expenses, recurring_expenses, "
    "holidays. Per-sender stores (private to the sender who owns them): reminders, "
    "agenda, briefing, monitors, scheduled_actions, personal_tasks, conversation. "
    "Distinguish household chores from personal tasks: the shared list_name='tasks' "
    "is for things the family shares responsibility for ('change the gas balloon', "
    "'buy smoke-alarm batteries', 'fix the front door lock'). Personal tasks (via "
    "add_personal_task) are things only the sender cares about ('open a bug on X', "
    "'email the boss', 'review PR from Dan') and are NEVER visible to other senders. "
    "Language cues: 'אני צריך' / 'לי' / 'בעבודה' / 'for me' → personal; "
    "'אנחנו צריכים' / 'בבית' / 'family' → household `tasks` list. Genuinely "
    "ambiguous: ask which. "
    "\n\n"
    "BROADCAST SEMANTICS. Expense tool outcomes (add_expense, delete_last_expense, "
    "fix_last_expense, expense_summary, add_recurring_expense, "
    "remove_recurring_expense) broadcast to the whole household — both senders "
    "see your reply. Everything else stays private to the sender who asked. When "
    "composing a broadcast reply, prefer neutral / plural phrasing that reads "
    "naturally to either recipient, and address the reporting sender by name if "
    "you know it rather than by pronoun. "
    "\n\n"
    "ENUM HEURISTICS. For add_expense / add_recurring_expense: pick the closest "
    "matching category from the fixed list; use 'אחר' ONLY when truly none fit. "
    "For payment_method: use 'לא צוין' when the user didn't say (do not guess). "
    "\n\n"
    "YEARLY REMINDERS ARE BRIEFING-ONLY. A reminder with recurrence='yearly' (e.g. "
    "a birthday) is surfaced inside the morning briefing on its date instead of "
    "firing as a standalone WhatsApp message. When you create one, phrase the "
    "confirmation to the user accordingly — 'I'll mention it in the morning brief "
    "on Oct 15' — do NOT promise a 09:00 ping. "
    "\n\n"
    "SUB-DAILY vs DAILY-OR-LONGER CADENCE. set_reminder only supports recurrence "
    "daily/weekly/monthly/yearly. For sub-daily cadences ('every hour', 'every 30 "
    "minutes', 'check in on me every 90 min') use schedule_check_in with "
    "interval_minutes. Never invent 'hourly' as a recurrence value. "
    "\n\n"
    "RECEIPT IMAGES vs OTHER IMAGES. A receipt is an image showing item lines "
    "and a total. Only that pattern triggers add_expense — a screenshot of a bank "
    "notification, a product photo, or any other image is described / answered "
    "normally without calling add_expense. On a receipt, use the grand total; only "
    "split when the user asks to split. "
    "\n\n"
    "ENTITIES. Only act on entities present in the Known devices list — never "
    "invent an entity_id for control_device, get_device_status, monitor_device, "
    "or schedule_action. If nothing in the catalog matches, tell the user rather "
    "than guessing. "
)

CLOSING = (
    "You work in a tool-use loop: after you call a tool you will be shown its result, and you "
    "may call more tools before answering. Chain steps when a task needs it — e.g. call "
    "get_device_status, read the result, then decide whether to act; or call list_reminders to "
    "read a reminder's exact time before rescheduling it. When you have finished what the user "
    "asked, reply with one short, natural confirmation of what you did — do not paste raw tool "
    "output, entity_ids, or internal ✅ strings verbatim; phrase it for a person. "
    "Reply in whatever language the user wrote in."
)

SYSTEM_PROMPT = PERSONA + TOOL_POLICY + DOMAIN_RULES + CLOSING

# Item 04: hybrid model routing. Interactive turns can run on Sonnet (cheaper,
# roughly identical behavior on the routine tool-dispatch path) while check-ins
# — which compose fresh outbound messages ~30-60 times/month — stay on Opus.
# The interactive choice is gated by settings.model_routing_hybrid (default off
# in the shipped add-on; user opts in until Item 17 validates the switch).
INTERACTIVE_MODEL_DEFAULT = "claude-opus-5"
INTERACTIVE_MODEL_HYBRID = "claude-sonnet-5"
CHECK_IN_MODEL = "claude-opus-5"
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
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional short tags describing what this anchor is (e.g. ['school'] for a school "
                                       "start/end time). Enables briefing filters like 'skip school anchors during chol hamoed'.",
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
            "name": _ADD_PERSONAL_TASK,
            "description": "Adds a task to the SENDER'S OWN private task list — never visible to other "
            "family members. Use for personal to-dos ('open a bug on X', 'email the boss'). For "
            "household chores that everyone shares, use add_to_list with list_name='tasks' instead.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The personal task text."},
                },
                "required": ["text"],
            },
        },
        {
            "name": _LIST_PERSONAL_TASKS,
            "description": "Shows the sender's own personal tasks (private). Does not include the shared "
            "household `tasks` list — use show_list(list_name='tasks') for that.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": _COMPLETE_PERSONAL_TASK,
            "description": "Marks a personal task done (removes it from the sender's private list), "
            "matched by a snippet of its text or its id.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Task text snippet or id."},
                },
                "required": ["text"],
            },
        },
        {
            "name": _CLEAR_PERSONAL_TASKS,
            "description": "Removes ALL of the sender's personal tasks at once.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": _SCHEDULE_CHECK_IN,
            "description": "Schedules a 'check-in': at the given time, ZOE wakes up, reads whatever fresh "
            "state your `prompt` tells her to read (lists, agenda, expenses, device status), and sends a "
            "dynamically composed WhatsApp message to the user. Distinct from set_reminder (static text) "
            "and from schedule_action (device command). Use for 'every evening at 18:00 check my task list "
            "and ask what's done', 'every Friday at 14 remind me about the weekend using the agenda', or "
            "'every hour ask me how progress is going on my open personal tasks'. The `prompt` field is "
            "an instruction to yourself for the moment of firing.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Short instruction to yourself for when the check-in fires. State "
                        "WHAT to read (e.g. 'read my open personal tasks') and WHAT MESSAGE to send the "
                        "user (e.g. 'ask about progress on each'). Written in the second person to future-you.",
                    },
                    "when": {
                        "type": "string",
                        "description": "ISO 8601 datetime for the FIRST fire, Israel time. Must be in the future.",
                    },
                    "recurrence": {
                        "type": "string",
                        "enum": ["daily", "weekly", "monthly", "yearly"],
                        "description": "Optional. Named recurrence for daily-or-longer cadences. Omit for "
                        "a one-time check-in or when using interval_minutes below.",
                    },
                    "interval_minutes": {
                        "type": "integer",
                        "description": "Optional. Fire every N minutes (e.g. 30, 60, 120). Use this for "
                        "sub-daily 'work-session' cadences like 'check on me every hour'. Overrides "
                        "recurrence when both are set. Minimum 15.",
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


def _system_blocks(closing_extra: str = "") -> list[dict[str, Any]]:
    """Assemble the system prompt as cache-marked content blocks.

    Each block is stable across turns, so Anthropic's prompt cache serves the
    prefix on the second and subsequent calls within the 5-min TTL. `closing_extra`
    lets check-ins append their suffix to the CLOSING block without breaking the
    shared PERSONA / TOOL_POLICY prefix cache with the interactive path.
    """
    return [
        {"type": "text", "text": PERSONA, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": TOOL_POLICY, "cache_control": {"type": "ephemeral"}},
        {
            "type": "text",
            "text": DOMAIN_RULES + CLOSING + closing_extra,
            "cache_control": {"type": "ephemeral"},
        },
    ]


def _build_cached_tools() -> list[dict[str, Any]]:
    """Build the tools list and mark the last entry with cache_control.

    Anthropic caches ALL preceding tool definitions as one block whenever the
    last entry carries a cache_control marker. Combined with the cache markers
    on `system`, the whole ~15k-token static prefix (system + tools) becomes a
    single cached prefix on the 2nd and later turns.
    """
    tools = _build_tools(_load_entities())
    if tools:
        tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}
    return tools


# Computed once at import time. The tool schemas depend only on
# config/entities.yaml — which is effectively stable for the add-on's
# lifetime, since editing it already requires an add-on restart.
_CACHED_TOOLS = _build_cached_tools()


def run_model(messages: list[dict[str, Any]]) -> Any:
    """One turn of the agentic loop: sends the running transcript and returns the raw
    Anthropic message (content blocks + stop_reason). The caller executes any tool_use
    blocks, appends the results, and calls again until stop_reason is not tool_use."""
    model = (
        INTERACTIVE_MODEL_HYBRID
        if settings.model_routing_hybrid
        else INTERACTIVE_MODEL_DEFAULT
    )
    resp = _client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=_system_blocks(),
        tools=_CACHED_TOOLS,
        messages=messages,
    )
    _log_usage("run_model", model, getattr(resp, "usage", None))
    return resp


def _build_cached_check_in_tools() -> list[dict[str, Any]]:
    """The check-in tool subset, with cache_control on the last entry.

    We filter the already-built full list (which drops the cache_control
    marker from the old last tool if it isn't in the subset) then re-apply
    the marker to the new last tool so the subset also caches.
    """
    subset = [
        {k: v for k, v in t.items() if k != "cache_control"}
        for t in _CACHED_TOOLS
        if t.get("name") in CHECK_IN_ALLOWED_TOOLS or t.get("type") == "web_search_20250305"
    ]
    if subset:
        subset[-1] = {**subset[-1], "cache_control": {"type": "ephemeral"}}
    return subset


_CHECK_IN_CACHED_TOOLS = _build_cached_check_in_tools()


def run_check_in_model(messages: list[dict[str, Any]]) -> Any:
    """One turn of a check-in agent loop, restricted to read-only tools."""
    resp = _client.messages.create(
        model=CHECK_IN_MODEL,
        max_tokens=MAX_TOKENS,
        system=_system_blocks(closing_extra=CHECK_IN_SYSTEM_SUFFIX),
        tools=_CHECK_IN_CACHED_TOOLS,
        messages=messages,
    )
    _log_usage("run_check_in_model", CHECK_IN_MODEL, getattr(resp, "usage", None))
    return resp


# Item 10: LLM-composed briefings. The deterministic renderer stays intact; when
# settings.briefing_model_compose is on, main.py hands the gathered data blob to
# this function instead so memory facts (e.g. "no school during chol hamoed") can
# actually shape the output. Model is fixed to Sonnet 5 — briefings are text
# composition and don't benefit from Opus reasoning; caps cost at ~$0.01/brief
# so 4 briefs/day is ~$1.20/month. No tools, no cache_control (setup cost isn't
# worth it for a one-shot small request).
_BRIEFING_SYSTEM = (
    "You are ZOE composing a WhatsApp brief for one household member.\n"
    "Compose ONE clear Hebrew message. Apply memory facts naturally (e.g. \"no school\n"
    "during chol hamoed\" -> omit school hours on those days). Do NOT invent items not\n"
    "in the data. Keep it scannable, no more than ~10 lines.\n"
    "\n"
    "Structure:\n"
    "- MORNING: greeting + today's items grouped by section (anchors, birthdays,\n"
    "  holidays, tasks) sorted by time when a time is given\n"
    "- EVENING: short today recap (one line) + tomorrow preview (few lines) +\n"
    "  spending summary if non-zero\n"
    "\n"
    "Reply with the message text ONLY. No prefix, no meta commentary."
)


def run_briefing_model(kind: str, data: dict[str, Any]) -> str:
    """Composes a morning ('morning') or evening ('evening') brief message from
    a structured data blob. Returns the final Hebrew text.

    kind: 'morning' or 'evening'. Included in the user turn so the model knows
    which structure to produce.

    Runs synchronously against the Anthropic SDK; main.py wraps the call in
    asyncio.to_thread + asyncio.wait_for(15s) so the daily briefing loop stays
    async and can't hang on a slow API response.
    """
    if kind not in ("morning", "evening"):
        raise ValueError(f"kind must be 'morning' or 'evening', got {kind!r}")
    user_text = (
        f"Compose the {kind.upper()} brief from this data. "
        f"Reply with only the Hebrew message text.\n\n"
        f"DATA:\n{json.dumps(data, ensure_ascii=False, indent=2)}"
    )
    resp = _client.messages.create(
        model=INTERACTIVE_MODEL_HYBRID,
        max_tokens=MAX_TOKENS,
        system=_BRIEFING_SYSTEM,
        messages=[{"role": "user", "content": user_text}],
    )
    _log_usage("run_briefing_model", INTERACTIVE_MODEL_HYBRID, getattr(resp, "usage", None))
    parts: list[str] = []
    for block in getattr(resp, "content", []) or []:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", "") or "")
    text = "".join(parts).strip()
    if not text:
        raise RuntimeError("run_briefing_model: model returned no text content")
    return text
