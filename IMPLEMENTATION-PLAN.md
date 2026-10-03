# ZOE — INSTINCT Refactor Implementation Plan

Living document. Every session (local or cloud) starts by reading this file to know where things stand. Every merged item flips a checkbox and records the commit + date at the bottom.

Target: turn Zoe from a working-but-brittle assistant into one that *feels* intuitive — applies memory proactively, chains tools naturally, remembers context across ticks — while cutting API cost ~50% and giving us a test net so future changes stop being live-fire on the household.

---

## Working rules (adapted from the user's pasted rules, adjusted for this project)

1. **Deployed Zoe never breaks.** The HA add-on version installed at home is the reference. `main` stays identical to that deployed version *until* a phase is fully merged, verified, and bumped to a new version.
2. **One branch per item** — `step-01-latent-bugs`, `step-02-prompt-caching`, `step-03-model-routing`, etc. No cross-item mixing. No item ever gets partially merged.
3. **Merge to main only after** the item's acceptance criteria are all met, the PR is reviewed with the user, and its tests pass. **`config.yaml` version bump + HA add-on deploy happens only at end-of-phase**, not mid-item.
4. **Small commits inside each PR** with descriptive messages — the plan doc says *what* and *why*; each commit says *how*. No big-bang commits.
5. **Feature flags** for anything user-facing that could regress: default off in the shipped version, opt-in for testing, flip default only after soak time.
6. **Progress log at the bottom** — every merged item logs its date + merge commit + notes.
7. **Cloud agents** get the plan doc as context and a self-contained brief for one item. They open a PR back to `main` from their own branch. They do NOT touch other items or version bumps.

---

## Budget

- **Available:** $100 in cloud-session credits (expires 2026-11-05)
- **Planned spend:** ~$85 across 12 items
- **Reserve:** $15 for surprises, revisions, or one bonus item

Estimates are rough — well-scoped cloud briefs typically finish under the allocated budget.

---

## Phase A — Correctness & Cost Wins (start here; no user-visible behavior change)

Highest ROI, lowest risk. Anything here that lands cleanly frees budget for the intelligence work in Phase C.

### [ ] Item 01 — Fix latent bugs (small, safe)
**Budget:** ~$4 · **Risk:** low · **Branch:** `step-01-latent-bugs`

Known latent issues found in the audit:
- `main.py::_handle_expense_call` has dead branch code that sets `source = "receipt" if ...` then immediately overwrites to `"manual"` — the `source` computation is broken. Fix: pick one, delete the other.
- `reminders.py::_next_occurrence` will **infinite-loop** if called with an invalid recurrence string on a past `send_at`: `_add_period` returns the datetime unchanged, and `while ts > now` never becomes true. Fix: raise on invalid recurrence at the top of `_next_occurrence`, or fall through to a single no-op return.
- `check_ins.py::_load` uses `CheckIn(**c)` which will raise `TypeError` if an older row has unknown extra keys. Same defensive `{k:v for k,v in c.items() if k in field_names}` pattern I put in `briefing.py::_from_dict` should be replicated.
- `settings.py::whisper_host` default is a hardcoded LAN IP (192.168.10.150). Move to `config.yaml` options so it survives an HA IP change without a code edit.

**Acceptance:** all four fixed; no behavior change on the happy paths; tests added for the two hot ones (see Item 03).

---

### [ ] Item 02 — Prompt caching + system prompt restructure
**Budget:** ~$8 · **Risk:** low · **Branch:** `step-02-prompt-caching`

Right now every model turn sends the full `SYSTEM_PROMPT` (~8k tokens) + all tool schemas (~15k tokens) fresh. Anthropic supports **prompt caching** — a marked block is stored server-side for 5 min and subsequent turns pay 10% of the token cost for a cache hit.

Actions:
- Split `SYSTEM_PROMPT` into stable sections (persona + tool guidance + policy) and one dynamic section (current datetime, device catalog, known facts). Stable sections marked with `cache_control: {"type": "ephemeral"}`.
- Tool schemas: also cache-able. `_build_tools` currently rebuilds every turn — hoist the static list up.
- Verify cache-hit rate on a debug endpoint or log line.

**Expected savings:** 40–60% on every non-first turn per user. Roughly cuts the monthly Anthropic bill in half. Also faster.

**Acceptance:** `stop_reason.cache_read_input_tokens > 0` on the 2nd turn of a conversation; end-to-end response quality unchanged in manual testing.

---

### [ ] Item 03 — Test scaffold + coverage for stores
**Budget:** ~$15 · **Risk:** low · **Branch:** `step-03-tests`

Zero automated tests today. Every change is a live-fire test at home. Add pytest with:
- `pytest` + `pytest-asyncio` + a `tmp_path`-based fixture that repoints `settings.*_path` at a per-test directory.
- Unit tests for **every store** (12 of them): add/list/find/remove/edge cases (empty, missing file, corrupt JSON, restart-preserves-state).
- Unit tests for the recurrence math in `reminders.py::_next_occurrence`, `check_ins.py::_next_from_interval`, `recurring_expenses.py::_due_today` (short-month clamp!).
- Unit tests for the two known bugs from Item 01 (regression protection).
- A GitHub Actions workflow that runs `pytest -q` on every PR (see Item 12).

**Acceptance:** `pytest -q` runs green locally and in CI; every store has ≥3 tests covering happy path, empty state, corruption tolerance.

---

### [ ] Item 04 — Hybrid model routing (Sonnet interactive, Opus check-ins)
**Budget:** ~$10 · **Risk:** medium (behavior sensitive) · **Branch:** `step-04-model-routing`

**Decided approach (user-approved):** hybrid.
- `claude-sonnet-5` for the interactive path — expense parsing, list ops, reminders, device control, agenda, anchors, all the routine tool dispatch.
- `claude-opus-5` for **check-ins only** — they compose fresh messages, benefit from the better model, and only fire ~30–60 times/month per user.
- Escalation hook: if Sonnet returns a refusal or errors on a specific pattern, route that pattern to Opus. Item 17's behavior harness surfaces which patterns need this.

Approach:
- Add `INTERACTIVE_MODEL` and `CHECK_IN_MODEL` constants in `claude_agent.py`.
- `run_model` uses `INTERACTIVE_MODEL`; `run_check_in_model` uses `CHECK_IN_MODEL`. Both routes benefit from Item 02's prompt caching.
- Behind a feature flag `model_routing_hybrid: bool` (default off in shipped version, on in test) so we can flip only after Item 17's harness confirms Sonnet handles the canonical scenarios ≥ 90%.

**Expected cost impact** (rough — real numbers on your Anthropic console):
- Current (Opus, no cache): ~$0.30/turn interactive
- After Items 02 + 04: ~$0.03/turn interactive, ~$0.12/check-in
- Monthly drop of ~80% at typical household usage.

**Acceptance:** Item 17's behavior harness passes ≥ 90% on the canonical set with Sonnet; check-ins still compose in the same style; feature flag flips default to on after a soak day at home.

---

### [ ] Item 05 — Generic JSON store base + migrate stores
**Budget:** ~$15 · **Risk:** medium · **Branch:** `step-05-store-base`

12 stores repeat the same pattern: `_load`, `_save`, dataclass, JSON dict/list, sender filtering. ~800 lines of near-identical boilerplate. Factor to:

```python
class Store(Generic[T]):
    def __init__(self, path: Path, cls: type[T]): ...
    def load(self) -> list[T]: ...
    def save(self, items: list[T]) -> None: ...
    def _mutate(self, fn: Callable[[list[T]], list[T]]) -> None: ...  # atomic-ish write
```

Migrate one store at a time (behind a feature flag if needed): `reminders → agenda → anchors → check_ins → expenses → recurring_expenses → monitors → scheduled_actions → personal_tasks → briefing → memory → lists`. Each migration keeps the module's public API unchanged (`add`, `list_for`, etc.) so nothing else breaks.

Also introduce **atomic writes** (write to `path.tmp`, `os.replace`) so a crash mid-write doesn't corrupt a store.

**Acceptance:** all 12 stores go through `Store`; all tests still pass; total LOC in `app/` drops by ~500 lines; a mid-write kill test verifies no corruption.

---

### [ ] Item 06 — Split `main.py` into handlers
**Budget:** ~$12 · **Risk:** medium · **Branch:** `step-06-split-main`

`main.py` is 59KB, ~1350 lines. Every new feature keeps growing it. Split into:

```
app/
  main.py                    # FastAPI app + startup + webhook + agent loop only
  loops.py                   # 5 background loops
  handlers/
    __init__.py              # DISPATCH_TABLE assembling all handlers
    reminders.py             # _handle_reminder_call
    lists.py                 # _handle_list_call
    memory.py                # _handle_memory_call
    monitors.py              # _handle_monitor_call
    scheduled_actions.py     # _handle_scheduled_action_call
    agenda.py                # _handle_agenda_call
    anchors.py               # _handle_anchor_call
    expenses.py              # _handle_expense_call
    check_ins.py             # _handle_check_in_call
    personal_tasks.py        # _handle_personal_task_call
    conversation.py          # _handle_conversation_call
    devices.py               # control_device + get_device_status
  briefing_compile.py        # _compile_morning_briefing + _compile_evening_briefing
```

Nothing changes semantically. Import paths update. `_dispatch_tool` becomes a table lookup instead of a chain of `if tool in X_TOOLS`.

**Acceptance:** all handlers moved, all tests pass, `main.py` under 300 lines.

---

## Phase B — Ops & DX

### [ ] Item 07 — Structured status endpoint + healthcheck
**Budget:** ~$4 · **Risk:** low · **Branch:** `step-07-status`

Right now checking if Zoe is healthy means SSH into HA + read logs. Add:
- `GET /health` → 200 with `{status: "ok", uptime: ..., version: ..., anthropic_reachable: bool, ha_reachable: bool, tunnel_last_check: ..., loops: {reminder: {last_tick, errors}, ...}}`.
- `GET /admin/status` (LAN-only, same guard as the old import endpoint) → richer view with counts per store, next scheduled fires, last sent messages.
- Cloudflare tunnel self-ping: from within Zoe hit her own public URL periodically; if it 530s, log with a specific tag so you can grep.

**Acceptance:** `curl http://192.168.10.150:8000/health` returns a useful JSON blob; a 5-minute Anthropic outage shows `anthropic_reachable: false` in the response.

---

### [ ] Item 08 — CI: GitHub Actions runs tests on PR
**Budget:** ~$2 · **Risk:** low · **Branch:** `step-08-ci`

Requires Item 03 to exist first.

- `.github/workflows/test.yml`: on `pull_request`, checkout, `pip install -r addon/requirements.txt`, `pip install pytest pytest-asyncio`, `cd addon && pytest -q`.
- Branch protection on `main`: require the check to pass before merge.

**Acceptance:** opening a PR triggers CI; a failing test blocks merge.

---

### [ ] Item 16 — 24h Meta window countdown warning
**Budget:** ~$4 · **Risk:** low · **Branch:** `step-16-meta-window`

The user is on Meta's free-tier phone number. Meta only allows outbound free-form messages within 24h of the recipient's last inbound message. Right now the window silently closes and Zoe just fails to deliver. Add a proactive warning.

- Track `last_inbound_at` per allowed sender (extend the existing `_seen_messages.json` or new small store).
- Small background loop (tick every 5 min): for each allowed sender, if `now - last_inbound_at >= 23h AND < 24h AND not warned_this_cycle`, send: "⏰ עוד שעה החלון של Meta נסגר. שלח לי משהו קצר ('היי') כדי לפתוח אותו מחדש."
- Optional second warning at 23h45m.
- Any inbound message resets `last_inbound_at` and clears `warned_this_cycle`.
- No warnings if the sender is inactive by design (no recent activity for days — don't ping the wife at 4am if she hasn't messaged in a week).

**Acceptance:** silent for 23h after any inbound; warns once at 23h; resets on next inbound; test with a mocked clock.

---

### [ ] Item 17 — Consistency audit (prompt restructure + behavior harness)
**Budget:** ~$8 · **Risk:** medium · **Branch:** `step-17-consistency`

Addresses the user's core complaint: "she's inconsistent, sometimes stupid." Two-part:

**Part A — Prompt audit:** the current `SYSTEM_PROMPT` is ~200 lines of layered rules. Cloud agent reads it critically, finds:
- Contradictions between sections (e.g. old set_reminder guidance vs new schedule_check_in guidance)
- Overlapping / redundant instructions
- Ambiguous places where the model has to guess
- Missing guidance for known failure patterns (yearly reminders, personal-vs-household tasks, broadcast semantics, gendered Hebrew)

Restructures into a cleaner sectioned prompt: `persona → tool policy → domain rules → language rules → safety`. Uses Anthropic's prompt engineering best practices (positive framing, examples for edge cases, clear precedence).

**Part B — Behavior harness:** ~20 canonical scenarios covering the common flows:
- Add expense (with & without payment method)
- Cancel reminder by description
- Ask "how much did we spend on X this month"
- "No soccer this Sunday" → suppress anchor
- "Add to shopping" vs "add to my tasks" — household vs personal
- Voice input transcription round-trip
- Receipt image → expense
- Multi-device control ("close all shutters")

Each scenario has an expected tool-call sequence. Run 3× per scenario to catch flakiness. Report shows pass rate per scenario.

**Acceptance:** pass rate ≥ 90% across canonical scenarios on the restructured prompt with Sonnet 5; regression suite lives in `tests/behavior/` and runs on demand (gated by env var since it costs API credits).

---

### [ ] Item 09 — `CLAUDE.md` for future sessions
**Budget:** ~$1 · **Risk:** none · **Branch:** `step-09-claude-md`

Root-level `CLAUDE.md` with:
- What Zoe is + tech stack in 3 lines
- Where the code lives (map of `app/`)
- How to run locally + deploy
- Where the plan lives (this file)
- The user's key preferences (English replies, gender-aware Hebrew, per-sender vs household boundaries)

Loaded automatically at the start of every future Claude Code session in this repo, so cloud agents and future me have context without me having to re-explain.

**Acceptance:** file exists; a fresh session confirms it can navigate the repo without me spoon-feeding paths.

---

## Phase C — Intelligence ("INSTINCT")

### [ ] Item 10 — Memory-aware briefings
**Budget:** ~$8 · **Risk:** medium · **Branch:** `step-10-memory-briefings`

Today's biggest UX gap: user says "during חול המועד there's no school" 5 times, memory saves it, briefings still list school hours. Root cause: `_compile_morning_briefing` and `_compile_evening_briefing` are pure Python — the model is never called during briefing composition, so no memory fact ever affects the output.

Fix: **pass** the compiled data blob (anchors + holidays + agenda + expenses + tasks + memory facts + current date) **to a Sonnet call** with a strict instruction:
> Compose today's morning brief for this user. Apply the memory facts naturally. Do not add anything not in the data. Return the message text only, in Hebrew.

Cost: ~$0.01/brief × 4 briefs/day = ~$1.20/month. Trivial.

Include a **feature flag** `briefing_model_compose: bool` (default off in shipped version, on in tests) so we can flip when comfortable.

**Acceptance:** with a memory fact "בחול המועד אין בית ספר", the school-hour anchors are omitted/annotated on Sukkot dates; test proves it deterministically by mocking the model call.

---

### [ ] Item 11 — Anchor tagging + school-aware suppression
**Budget:** ~$6 · **Risk:** medium · **Branch:** `step-11-anchor-tags`

Complementary to Item 10 — even without an LLM brief, the anchor system itself can be smarter:
- Add optional `tags: list[str]` to `Anchor`. Model tags school-related anchors as `["school"]` when creating.
- `add_anchor` tool schema gains a `tags` array.
- `anchors_for_date(day, date, exclude_tags_on_holiday_off=True)` — when the day's Hebcal holiday has `school_status=="off"`, filter out `["school"]`-tagged anchors and prepend a note "🎒 [holiday] — אין בית ספר היום".
- **Migration:** heuristic auto-tag existing anchors whose text contains "בית ספר", "מסיימת", "כיתה".

**Acceptance:** on a Sukkot chol-hamoed date, the brief omits school-tagged anchors; on a normal Tuesday nothing changes; migration auto-tags known anchors.

---

### [ ] Item 12 — Check-in continuity (remember last exchange)
**Budget:** ~$5 · **Risk:** low · **Branch:** `step-12-checkin-continuity`

Hourly check-ins are currently stateless — the 16:00 check asks "how's progress on X" identically to how 15:00 did, even if the user just replied "still working on it" at 15:15.

Fix: when `_run_check_in` fires, prepend to its context:
- The last 2–3 exchanges from `conversation.recent(sender)` filtered to this check-in's topic (or just: last check-in output + user reply to it).
- The check-in's own history: prior fires of this specific check-in (add `last_fired_response: str` to `CheckIn`).

The model now knows what it just asked and what the user said, so the next ping is contextual: "עדיין עובד על X? עברה שעה מאז" instead of "how's X going?" again.

**Acceptance:** in a manual scenario — set 15m interval, reply "still working" — the next fire references "you were still working on it 15m ago".

---

### [ ] Item 13 — Cross-store name resolution + display helpers
**Budget:** ~$4 · **Risk:** low · **Branch:** `step-13-name-resolution`

Today, `expense_summary`'s `by_sender` block returns raw phone numbers. The model is *told* to translate via memory facts, but often forgets. Also lists, tasks, and reminders show phones in the household views.

Fix: single `senders.resolve_name(phone) -> str` helper that reads memory facts once, caches for the request. Every place that renders a phone goes through it. Model no longer has to translate.

**Acceptance:** every household-visible message that could show a phone shows a name instead when a memory fact links them; unknown numbers fall back gracefully.

---

### [ ] Item 14 — HA `get_states` parallelism + bulk endpoint
**Budget:** ~$3 · **Risk:** low · **Branch:** `step-14-ha-parallel`

`HomeAssistantClient.get_states(entity_ids)` currently makes N sequential HTTP calls (one per entity). With ~20 known entities that's ~2 seconds of blocking on every message. Options:
- **Bulk:** `GET /api/states` returns ALL entities; filter in Python. One HTTP call. Simpler.
- **Parallel:** `asyncio.gather()` the N calls. Faster if `/api/states` is slow on HA.

Pick bulk (simpler, matches HA's normal usage).

**Acceptance:** median time from webhook receive → first model token drops noticeably; test that only requested entity IDs come back in the returned dict.

---

## Phase D — Bonus (if credit budget survives)

### [ ] Item 15 — Ultra code-review pass
**Budget:** ~$10 · **Risk:** none · **Branch:** N/A (comments only)

Once phases A–C land, run `/code-review ultra` against the refactored codebase. Cloud agent produces a review of what we shipped. Findings feed a follow-up small-items PR.

**Acceptance:** review report attached to the plan; any confirmed correctness findings get a follow-up branch.

---

## Phase E — Email post-Phase D (queued, not launched)

### [ ] Item 22 — Spam auto-triage with scheduler
**Budget:** ~$3 (implementation, Sonnet agent) · **Risk:** medium (writes to the live inbox) · **Branch:** `step-22-spam-triage`

Builds on Items 19–21. Teaches ZOE to triage spam in the household inbox on a schedule the user owns, moving confident spam to the provider's Spam folder and asking about the uncertain ones over WhatsApp. **Not** hard-delete — move-to-Spam only (recoverable for 30 days on Gmail/Yahoo/iCloud).

**New IMAP write methods** (`addon/app/email_imap.py`):
- `move_to_spam(uid)`, `move_to_folder(uid, folder)`, `mark_read(uid)`
- Auto-detect the provider's Spam folder name (Gmail `[Gmail]/Spam`, Yahoo `Bulk Mail`, iCloud `Junk`) via `LIST` on first run; override via `email_spam_folder` setting if detection fails.
- Guard: all write methods noop if `settings.email_write_enabled` is False (default). Nothing destructive happens before the user opts in.

**New store** (`addon/app/spam_scan_schedule.py`): per-household (not per-sender) `SpamScanSchedule` row with `start_hour`, `end_hour`, `interval_hours`, `tz` (default `Asia/Jerusalem`), `enabled`. Managed by Claude tools, NOT by `config.yaml` options:
- `set_spam_scan_schedule(start_hour, end_hour, interval_hours)` — "scan 8–20 every 2h"
- `show_spam_scan_schedule()`, `disable_spam_scan()`, `enable_spam_scan()`

**New store** (`addon/app/spam_sender_memory.py`): learned verdicts `{from_addr → "spam" | "legit", confidence, last_updated}`. Populated by user confirmations and auto-moves. The scanner checks this FIRST before calling the LLM — most of a steady-state inbox routes without a model call.

**New store + audit log** (`addon/app/spam_audit.py`): every scan decision logged — `{uid, from, subject, verdict, confidence, action, timestamp}`. Rolling 30-day retention. Lets the user review what was moved during dry-run.

**Classifier module** (`addon/app/spam_classifier.py`):
- Model: **Haiku 4.5** (`claude-haiku-4-5` — $1/$5 per MTok, plenty for this binary call).
- Input: sender + subject + first ~200 chars of snippet (not full body — ~1k tokens total).
- **Prompt caching** on the system prompt (classifier instructions are fixed → cache reads ~$0.03/MTok on Haiku).
- **Batch of 5–10 emails per call** when the scan has that many candidates — shared system prompt only paid once.
- Returns `{verdict, confidence, reason}` per email.
- Decision table:
  - sender memory hit → use memory, no LLM call
  - LLM `verdict=spam` + `confidence ≥ 0.9` → move to Spam + log + update sender memory
  - LLM `0.6 ≤ confidence < 0.9` OR `verdict=uncertain` → WhatsApp confirmation (reuse `confirmation.py`): `💭 ספאם? "<subject>" מ-<sender>. כן/לא`. On yes/no, act + update sender memory.
  - LLM `verdict=legit` OR `confidence < 0.6` → do nothing

**Dry-run week:**
- New setting `email_scan_dry_run: bool = True` (default ON).
- When ON: scanner runs classifier + logs to audit, but **doesn't move anything** and **doesn't ask the user**. First week lets the user review the audit log and spot bad calls before going live.
- New tool `show_spam_audit(days=7)` to review decisions.
- User flips to False via a new tool `enable_spam_live_mode()` (deliberate step, not a flag flip in HA UI).

**Scheduled scan loop** (`addon/app/loops.py`):
- Reads the schedule from `spam_scan_schedule.json` each tick.
- Only acts when `now` is within `[start_hour, end_hour)` in the schedule's TZ AND `now - last_scan_at >= interval_hours`.
- Scans **unread** inbox (`IMAP SEARCH UNSEEN`) since last scan.
- Dedup against audit log by UID.
- Allowlist check: `settings.email_spam_allowlist` (comma-separated patterns) never touched regardless of classifier output. New tool `add_spam_allowlist(sender_pattern)`.
- Heartbeat on `/admin/status` like Item 21's watch loop.

**Settings additions:**
- `email_write_enabled: bool = False`
- `email_scan_dry_run: bool = True`
- `email_spam_folder: str = ""` (blank = auto-detect)
- `email_spam_allowlist: str = ""` (comma-separated)
- `email_spam_classifier_model: str = "claude-haiku-4-5"` (overridable if Haiku misbehaves)

**Target runtime cost:** ~$2/month at ~120 emails/day scanned with sender memory warm (most emails skip the LLM entirely).

**Acceptance:**
- Dry-run week: audit log shows classifier verdicts, nothing moved, nothing broadcast.
- Live mode: `email_write_enabled=true` + `email_scan_dry_run=false` → confident spam moves to Spam, uncertain prompts WhatsApp, user yes/no completes the loop.
- Schedule set via WhatsApp (`"סרקי ספאם בין 8 ל-20 כל שעתיים"`) persists across restarts.
- All existing tests still pass; new tests cover classifier batching, sender memory hit/miss, dry-run gating, schedule window logic, allowlist bypass.

**Not launched yet** — queued per user decision 2026-10-03: insufficient Pro plan weekly token budget this cycle to safely finish without the agent being cut off mid-run.

---

## Budget summary

| Item | Budget | Cumulative |
|---|---|---|
| 01 Latent bugs | $4 | $4 |
| 02 Prompt caching | $8 | $12 |
| 03 Test scaffold | $15 | $27 |
| 04 Hybrid model routing | $10 | $37 |
| 05 Generic store | $15 | $52 |
| 06 Split main.py | $12 | $64 |
| 07 Status endpoint | $4 | $68 |
| 08 CI | $2 | $70 |
| 09 CLAUDE.md | $1 | $71 |
| 10 Memory briefings | $8 | $79 |
| 11 Anchor tags | $6 | $85 |
| 12 Check-in continuity | $5 | $90 |
| 13 Name resolution | $4 | $94 |
| 14 HA parallel | $3 | $97 |
| **16 Meta window warning** (new) | **$4** | **$101 — over** |
| **17 Consistency audit** (new) | **$8** | **$109 — over** |
| 15 Ultra review (bonus) | $10 | $119 |

Nominal sum ($119) exceeds the $100 pot on paper. In practice, well-scoped cloud briefs typically land at 60–80% of their estimate, so realistic net spend is ~$85–95. If we're tracking against budget after Phase A merges (items 01–06), we tighten or defer bonus items 15 / 17 to stay in envelope.

Real budget reconciliation happens after each merge: I log actual credit spend into the Progress log below.

---

## Recommended execution order

1. **01 → 02 → 03** sequentially (each depends slightly on the prior; 03 unlocks safe parallelism after).
2. **04, 07, 09, 14, 16** can run in parallel (independent, small).
3. **05 → 06** sequentially (05's `Store` class simplifies 06's split).
4. **17** after 04 (its harness verifies the hybrid routing) — feeds targeted fixes.
5. **10 + 11** together (both touch briefing compile).
6. **12, 13** in parallel (independent).
7. **08** any time after 03 lands.
8. **15** last, if budget survives.

Parallel execution: up to 3 cloud agents concurrently.

---

## Deploy cadence

- **After Phase A (items 01–06) merged:** version bump to `0.21.0`, HA add-on deploy.
- **After Phase B (07–09) merged:** version bump to `0.22.0`, deploy.
- **After Phase C (10–14) merged:** version bump to `0.23.0`, deploy.
- **Bonus (15):** whatever it produces feeds a `0.23.1` patch if needed.

Never bump/deploy mid-phase.

---

## Progress log

| Item | Merged | Commit | Notes |
|---|---|---|---|
| 01 | 2026-09-29 | `9122415` | 4 latent bugs; defensive `_from_dict` across all 11 stores |
| 02 | 2026-09-29 | `c63abf0` | Prompt caching (system + tools); byte-identical restructure |
| 03 | 2026-09-30 | `a4341cf` | 168 tests across 14 stores + recurrence + regressions; all pass on CI |
| 04 | 2026-09-29 | `601e374` | Hybrid routing flag `model_routing_hybrid` (default OFF) |
| 05 | 2026-09-30 | `81103e5` | Generic `Store[T]` base; all 15 stores migrated; -12 LOC net |
| 06 | 2026-09-30 | (merged) | Split main.py: 1818→188 lines; new handlers/, loops.py, agent_loop.py, briefing_compile.py, status.py |
| 07 | 2026-09-30 | `7ce7baf` | `/health` + `/admin/status` LAN-only; loop heartbeats |
| 08 | 2026-09-30 | `0b148c3` | GH Actions runs pytest on PR; **168/168 passing** |
| 09 | 2026-09-29 | `5c63c8a` | Root `CLAUDE.md` for future sessions |
| 10 | 2026-09-30 | `925ae84` | LLM-composed briefings behind `briefing_model_compose` flag (default OFF) |
| 11 | 2026-09-30 | `b08ae0c` | Anchor `tags` field + `school` filter on Jewish-holiday school-off days |
| 12 | 2026-09-30 | `3314777` | Check-in continuity: prior ping + user reply feed into next tick |
| 13 | 2026-09-30 | `790cc40` | Phone → name resolver from memory facts; used in summaries/logs |
| 14 | 2026-09-29 | `9097b67` | Bulk `GET /api/states`; ~2s → ~150ms per message |
| 15 | 2026-09-30 | (merged) | Ultra review: no critical/high findings; M1 + M2 fixed in follow-up |
| 16 | 2026-09-29 | `be752e1` | Meta 24h warning at ~23h (rebase-fixed mid-flight) |
| 17 | 2026-09-30 | `2f86b99` | Prompt restructure into precedence-ranked sections; 24-scenario harness |
| 18 | 2026-10-03 | `36aa4ca` | Voice: fire-and-forget ack + OpenAI Whisper API (Wyoming fallback) |
| 19 | 2026-10-03 | `baf49a0` | Email foundation: `EmailBackend` protocol + IMAP + 3 read-only tools |
| 20 | 2026-10-03 | `c81cebe` | Email intelligence: receipt + iCal auto-extract behind `email_auto_extract` flag (default OFF) |
| 21 | 2026-10-03 | `d0c3af9` | Email watch loop: hourly ticks, per-watch `interval_minutes` override, UID dedup |
| 22 | — | — | queued (spam auto-triage: Haiku classifier + sender memory + user-set schedule + dry-run week → ~$2/mo runtime) |
