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

### [ ] Item 04 — Model routing (Sonnet for parsing, Opus for reasoning)
**Budget:** ~$10 · **Risk:** medium (behavior sensitive) · **Branch:** `step-04-model-routing`

Currently every turn uses `claude-opus-5` at ~$0.15/turn. Many turns are simple: "add tomatoes to shopping" is one `add_to_list` call — Sonnet 5 handles it identically at ~1/5 the cost.

Approach — **triage** step (very cheap Sonnet call):
1. First model call: `claude-sonnet-5` with the full agent loop.
2. If Sonnet finishes in 1 iteration with no complex reasoning, done — cost ~$0.03.
3. Escalate to Opus only when Sonnet either:
   - Returns `stop_reason=refusal` or errors
   - The user's message is flagged as complex (multi-step planning, ambiguous, memory-heavy) via a simple classifier
   - The check-in agent (which composes messages, benefits from Opus)

Alternative simpler approach: use Sonnet **always** for the interactive path, keep Opus **only** for the check-in agent. Test whether Sonnet is good enough for the household. Cheaper to build and evaluate.

**Acceptance:** monthly cost drops by ~50–70% in interactive turns; regression tests for reminder-creation, expense-parsing, and control-device intent all still pass.

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

## Budget summary

| Item | Budget | Cumulative |
|---|---|---|
| 01 Latent bugs | $4 | $4 |
| 02 Prompt caching | $8 | $12 |
| 03 Test scaffold | $15 | $27 |
| 04 Model routing | $10 | $37 |
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
| — Reserve | | $3 left of $100 |
| 15 Ultra review | $10 | *dips into reserve if all above land under budget* |

Reality: the sum is aggressive because a well-scoped cloud brief often lands well under estimate. If we're on track after Item 06, most of Phase C is already funded.

---

## Recommended execution order

1. **01 → 02 → 03** sequentially (each depends slightly on the prior; 03 is what unlocks safe parallelism after).
2. **04, 07, 09, 14** can run in parallel (independent, small).
3. **05 → 06** sequentially (05's `Store` class simplifies 06's split).
4. **10 + 11** together (both touch briefing compile).
5. **12, 13** in parallel (independent).
6. **08** any time after 03 lands.
7. **15** last.

Parallel execution: I can launch 3 cloud agents concurrently. Sequential means we wait for a merge before starting the next.

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
| 01 | — | — | — |
| 02 | — | — | — |
| 03 | — | — | — |
| 04 | — | — | — |
| 05 | — | — | — |
| 06 | — | — | — |
| 07 | — | — | — |
| 08 | — | — | — |
| 09 | — | — | — |
| 10 | — | — | — |
| 11 | — | — | — |
| 12 | — | — | — |
| 13 | — | — | — |
| 14 | — | — | — |
| 15 | — | — | — |
