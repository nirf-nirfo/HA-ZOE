# PROMPT_AUDIT — Item 17 Part A

A critical read of the current `SYSTEM_PROMPT` in `addon/app/claude_agent.py`
(as of Item 02's split into `PERSONA` + `TOOL_POLICY` + empty `DOMAIN_RULES` +
`CLOSING`). Purpose: surface every point where the model has to guess, then feed
those into the restructure done in commits A2 and A3.

The prompt is one large monolithic paragraph inside `TOOL_POLICY` (~180 lines).
Everything below refers to that block unless noted.

## 1. Direct contradictions

- **`set_reminder` "VERBATIM" clause vs. `schedule_check_in`.**
  `set_reminder.text` is described as delivered "VERBATIM" as a WhatsApp message
  — no dynamic composition. Later, `schedule_check_in` is introduced as the
  right tool for that. Both descriptions are correct in isolation but the model
  has to hold two ~150-word chunks in working memory to compare them.
  The prompt _tells_ the model to pick `schedule_check_in` for dynamic
  composition, but the older `set_reminder` block still has anti-patterns
  ("NEVER write it as a description of what YOU (ZOE) will do") without a
  positive pointer next to it. Symptom: the model still occasionally builds
  ZOE-internal-plan `set_reminder` texts.

- **Household `tasks` list vs. personal tasks.**
  `add_to_list` says lists (including `tasks`) are "shared between all family
  members." A separate later paragraph re-splits it into household vs. personal
  and gives language cues. If the model reads top-to-bottom it will conclude
  `add_to_list(list_name='tasks', ...)` is always fine before it reaches the
  disambiguation.

- **`kind` filter defaults on `list_reminders`.**
  Prompt says `list_reminders` "By default returns all of them, grouped by
  repeat interval." Later text tells the model to `pass the matching kind so
  the recurring ones don't bury the one-time ones`. These aren't strictly
  contradictory but the second phrasing pushes the model toward always passing
  a filter, causing it to guess `kind` when the user said "my reminders" with
  no qualifier.

## 2. Overlapping / redundant instructions

- **Israel timezone.** Referenced 4 times: in `set_reminder`, `add_agenda_item`,
  `schedule_action`, `add_anchor`, `set_daily_briefing`, `monitor_device`. All
  say the same thing ("Israel time / Asia/Jerusalem"). One rule at the top of
  DOMAIN_RULES suffices.

- **Hebrew gendered address.** Currently mid-block ("If a Known fact links that
  number to a person and their gender…"). Also referenced in the expense
  summary block ("translate sender phone numbers to names"). Two separate
  rules on the same domain topic.

- **Expense category / payment enum warning.** The `add_expense` paragraph
  spells out the full enum and says `use 'אחר' only when truly none fit`. The
  `add_recurring_expense` paragraph then repeats the full enum. The enum itself
  is in the tool schema (`enum: […]`); the tool schema alone communicates the
  allowed values. Only the "pick the closest match, use 'אחר' only when truly
  none fit" heuristic needs to live in prose — and once, not twice.

- **Broadcast semantics.** Never named explicitly in the prompt today. The code
  handles it (BROADCAST_TOOLS in claude_agent.py, dispatcher in main.py), but
  the model isn't told which of its actions are visible to the whole household.
  This is a missing rule and it also means the model composes wife-facing
  copy without knowing when the wife will see it.

- **"Do not just tell the user to message you again."** Buried in
  `schedule_action`; the same anti-pattern applies to `schedule_check_in`,
  `set_reminder` and `monitor_device` — the model regularly says "message me
  at X and I'll do Y" instead of scheduling.

## 3. Ambiguities where the model has to guess

- **List-name canonicalisation.** "grocery/shopping" → `shopping`,
  "משימות/tasks/to-do" → `tasks`, "everything else" → user's own name. But
  what about `קניות` (Hebrew "shopping" without `רשימת` prefix)? What about
  `בקבוקים לחזור`? The rule as written is a rule about the canonical two;
  the "keep it consistent for that same list across messages" line is a
  separate rule about de-duplication. Both need explicit examples.

- **Yearly reminders as briefing-only.** The prompt says
  `yearly reminders (like birthdays) are surfaced in the morning briefing on
  their date instead of firing as standalone messages`. But the `set_reminder`
  paragraph still tells the model "yearly" is a valid recurrence and to write
  a verbatim text. So a user says "יום הולדת לאמא ב-15 באוקטובר" — model
  writes `text='יום הולדת לאמא!' send_at='2026-10-15T09:00:00' recurrence='yearly'`.
  On 15 Oct the user gets no 9am ping, only a morning-brief mention.
  The user will complain the reminder "didn't work."
  We should either (a) tell the model explicitly not to promise a 9am ping for
  yearly reminders and to phrase confirmation as "I'll mention it in the
  morning brief on Oct 15", or (b) let yearly ones fire as messages. Choosing
  (a) here — codified in DOMAIN_RULES.

- **`recurrence` on set_reminder vs. `interval_minutes` on schedule_check_in.**
  For "every hour", the prompt tells the model to use `schedule_check_in` +
  `interval_minutes`. There is no hour-granular option on `set_reminder`
  (`recurrence` only has daily/weekly/monthly/yearly). But the model
  occasionally attempts `set_reminder` with an ambiguous `recurrence='hourly'`
  which is rejected by the enum. Should be called out as a decision rule:
  sub-daily cadence → `schedule_check_in`; daily-or-longer plain text →
  `set_reminder`.

- **`sender_only` on expense tools.** The default is household-wide. The user
  frequently means "just me" — "כמה הוצאתי החודש" is ambiguous between
  "we" and "I". The prompt is silent about which pronoun triggers `sender_only`.

- **Ambiguous device intents.** "תסגרי הכל" — close all covers only, or all
  covers + turn off lights + AC? Not addressed.

- **Receipt image handling for grand total vs. per-line.** Prompt says "use the
  grand total unless the user asks to split" but "visibly separate purchases"
  is vague — two people at one supermarket with their items separately rung
  is one receipt with one grand total; the model has sometimes split. Needs a
  positive example.

## 4. Missing guidance for known failure patterns

- **Yearly reminders "silent".** As above.

- **Gendered Hebrew when composing a message the wife will see.**
  Broadcast expense summaries get sent to both senders. The current gender
  rule says "if the sender is female use feminine forms" — but a broadcast
  message goes to _both_ genders. No guidance on how to phrase household-wide
  broadcasts (options: neutral plural, or address the reporting sender by
  name and let the other see it in third person).

- **Household vs. personal task cues.** Currently present but at the end of
  a paragraph rather than as a decision list. Also missing:
  - "I need to buy X for the kids" → household (shared shopping).
  - "I need to remember to submit expense report" → personal.

- **Receipt-image handling.** Only addressed inside `add_expense` prose. A
  non-receipt photo path is mentioned ("describe/answer normally") but the
  model still occasionally logs an expense from a screenshot of a bank app.
  Rule should be: only images that visibly show item lines + a total are
  receipts; a bank-notification screenshot is described, not logged.

- **Broadcast semantics** (see above) — the model never learns that
  `add_expense`, `expense_summary`, etc., will be seen by both senders. This
  affects tone.

- **Reminder time defaults.** "birthday for mom on Oct 15" → what time? The
  model picks 09:00 arbitrarily. Should either be a rule ("default 09:00
  local for dated reminders without a time") or the model should ask.

- **"NEVER invent an entity_id"** is stated once for `control_device`. Same
  rule applies to `get_device_status`, `monitor_device`, `schedule_action`.
  The model very occasionally makes up an entity_id when the request is
  slightly out of catalog.

## 5. Structural observations

- No section headings. The whole `TOOL_POLICY` reads as one wall of prose. A
  model that has to answer under 300 ms benefits from named subsections it
  can skim.
- No precedence rule. When the model reads two rules that both apply — say,
  broadcast policy vs. tone — there's nothing telling it which wins.
- Domain rules and tool guidance are interleaved. Timezone, language,
  household/personal split, enums — these are domain properties of the
  household, independent of any specific tool. Moving them to DOMAIN_RULES
  isolates "how the world works" from "how to call tools."

## Restructure plan (implemented in A2 and A3)

**A2: DOMAIN_RULES** picks up:
- Time & timezone (Israel / Asia/Jerusalem).
- Language: reply in the user's language; Hebrew gendered forms; broadcast
  wording.
- Household vs. per-sender data model (including the household `tasks` list
  vs. `add_personal_task`).
- Broadcast semantics — the expense tool set is household-visible.
- Enum guidance heuristics (category "אחר" only as last resort;
  `payment_method='לא צוין'` when not stated).
- Sender resolution (translate phone → name via `Known facts`).
- Yearly reminders are briefing-only — phrase confirmation accordingly.

**A3: TOOL_POLICY** gets a precedence block at the top, and each tool group
is a short section with a fixed shape:

```
### <tool group>
What it does. When to call. When NOT to call. Edge cases / gotchas.
```

Same rules, no drops — just reordered, deduped and named.
