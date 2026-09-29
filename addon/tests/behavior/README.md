# Behavior harness

Canonical scenarios that pin ZOE's tool-routing decisions to expected calls.
Item 17's answer to the "she's inconsistent" complaint: instead of live-firing
each prompt change on the household, we replay a fixed set of user messages
through the ACTUAL `run_model` and check that the model picked the right tool
with the right arguments.

## Scope

- Exercises the real `app.claude_agent.run_model`, which means the current
  `SYSTEM_PROMPT` + tool schemas + model routing setting (see the flag note
  below) — no mocks.
- Does NOT execute the returned tool calls. This is a routing test, not an
  end-to-end test. The "she called `add_expense` with amount=150 and
  category=סופר" assertion is what the harness cares about.
- The unit-test scaffold under `addon/tests/` (Item 03) is untouched by this
  harness. Those tests are free; these tests cost API credits.

## How to run

```sh
# One-off from repo root:
cd addon
ANTHROPIC_API_KEY_TEST=sk-ant-... pytest -q tests/behavior -s

# Or the raw script (same scenarios, no pytest wrapper):
ANTHROPIC_API_KEY_TEST=sk-ant-... python -m tests.behavior.runner
```

Without `ANTHROPIC_API_KEY_TEST` set, every test in `tests/behavior/` is
skipped with a message pointing here. That guard is why the harness is safe
to leave in the repo — a plain `pytest` never touches Anthropic.

## Model routing

The harness runs against whatever `settings.model_routing_hybrid` is set to
in the process environment — same as production. Item 04's flag chooses
between:

- `INTERACTIVE_MODEL_DEFAULT` (Opus) — the current shipped default.
- `INTERACTIVE_MODEL_HYBRID` (Sonnet) — the flag-on path.

Before flipping the default in `settings.py`, run the harness once with each
setting and confirm the Sonnet path clears the 90% bar too:

```sh
# Baseline: current shipped path (Opus)
ANTHROPIC_API_KEY_TEST=... pytest -q tests/behavior -s

# Candidate: hybrid path (Sonnet)
ANTHROPIC_API_KEY_TEST=... MODEL_ROUTING_HYBRID=1 pytest -q tests/behavior -s
```

(How `settings.model_routing_hybrid` reads its env var is defined in
`addon/app/settings.py`. Adjust the env-var name in the command above if
that setting expects a different one.)

## Cost estimate

Each scenario sends one turn of ~500–1500 input tokens (the full system
prompt is charged on the FIRST call, cached on subsequent calls) and gets a
short tool_use response. With `runs=3` and 24 scenarios, that's 72 API
calls.

- **Sonnet path (hybrid on):** roughly **$0.30 per full harness run**.
- **Opus path (default):** roughly **$1.50 per full harness run**.

Numbers are ballpark — real cost is on the Anthropic console. Prompt caching
(Item 02) means the second and later scenarios in a single harness run pay
~10% of the system-prompt cost.

## Not in CI

The harness is deliberately manual — `.github/workflows/` never invokes it.
Rationale: CI runs on every PR, and even a $0.30 run on every push adds up
fast, plus a rate-limit or transient Anthropic outage would produce
non-deterministic red builds. Run it locally when:

- Editing `SYSTEM_PROMPT`, `TOOL_POLICY`, `DOMAIN_RULES`, or tool schemas.
- Changing the model routing flag default.
- Adding a new tool or renaming an existing one.

## Adding scenarios

New scenarios go at the bottom of `scenarios.py` — never renumber or delete
existing ones, so pass-rate diffs across time stay meaningful. Fields:

```python
{
    "name": "add_expense_shorthand",   # stable id
    "user_message": "150 סופר",         # what the user sends
    "expected_tool_call": "add_expense",# tool name, or None for free-form
    "expected_args_contains": {         # subset match
        "amount": 150,
        "category": "סופר",
    },
    "runs": 3,                          # optional; default 3
    "notes": "...",                     # optional; humans only
}
```

Argument matching:
- Scalars: exact equality (case-insensitive substring for strings).
- Dicts: subset match, recursively (one level).
- Use `"__any__"` as the expected value when you only care the key is
  present (e.g. `date` for "tomorrow" scenarios, where the exact date
  depends on the run day).
- Omit `expected_args_contains` entirely when the tool call itself is the
  only assertion (e.g. `list_reminders` with no filter).
