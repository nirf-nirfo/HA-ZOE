# ZOE — Repo Guide for Claude Code Sessions

Auto-loaded at the start of every Claude Code session in this repo. Keep it factual and terse so a cloud agent can start working without a re-briefing.

## What ZOE is

WhatsApp assistant for Home Assistant, packaged as a Home Assistant add-on. Runs a Claude tool-use loop over a Python + FastAPI backend: WhatsApp webhook -> transcribe/parse -> Anthropic Messages API with a tool schema over the household's devices and stores -> reply on WhatsApp and/or actuate HA. Users are one family (Nir + wife). Deployed at home on their HA Green.

## Where things live

- `addon/app/` — Python source (one module per domain; see Architecture below).
- `addon/config.yaml` — HA add-on manifest. The `version:` here is the deployed version. **Only bump at end-of-phase per `IMPLEMENTATION-PLAN.md`, never per item.**
- `addon/config/entities.yaml` — device catalog Zoe is allowed to see and touch. Cached at import; changes need an add-on restart.
- `addon/tests/` — pytest suite (added in Item 03; may not exist yet when you read this).
- `addon/requirements.txt` — Python deps.
- `addon/run.sh` + `addon/Dockerfile` — add-on runtime entry point and container build.
- `addon/.env.example` — local-dev environment variables.
- `IMPLEMENTATION-PLAN.md` — the INSTINCT refactor plan. **Always read this first if you're doing refactor work.** Working rules, per-item briefs, budget, and progress log live there.

## Architecture in one screen

Entry point / orchestration:
- `main.py` — FastAPI app, `/webhook` handler, agent loop, and five background loops (reminders, briefings, monitors, scheduled actions, check-ins). ~1350 lines today; Item 06 splits it.
- `claude_agent.py` — Anthropic client wrapper, `SYSTEM_PROMPT`, and all tool schemas (`*_TOOLS` groups + `_build_tools`).

Stores — all JSON-backed under `/data/*.json` (each module owns its own load/save + dataclass; Item 05 factors this out):
- `reminders.py` — timed messages, one-shot or recurring.
- `agenda.py` — items pinned to a specific calendar day; surfaced in the morning brief.
- `anchors.py` — weekly-recurring household schedule (e.g. Sundays 13:00 school pickup); with per-date suppressions.
- `expenses.py` — household-wide expense ledger.
- `recurring_expenses.py` — monthly/subscription templates that auto-post to `expenses`.
- `monitors.py` — periodic device-state checks with edge-triggered alerts.
- `scheduled_actions.py` — device commands queued to run at a future time (distinct from a reminder).
- `check_ins.py` — self-scheduled "wake up and ask about X" pings, one-shot or interval-based.
- `personal_tasks.py` — per-sender private to-do list (parallel to household `lists`).
- `lists.py` — household lists (shopping, tasks, etc.).
- `memory.py` — durable household facts (preferences, names, defaults).
- `briefing.py` — per-sender morning + evening brief schedule.
- `conversation.py` — short-term per-sender turn buffer (24h / rolling cap).
- `conversation_log.py` — 90-day append-only searchable exchange log.

Integrations:
- `whatsapp.py` — Meta Cloud API send + webhook signature verify.
- `ha_client.py` — Home Assistant REST client (`get_state`, `get_states`, `call_service`).
- `transcribe.py` — voice-note transcription via a Wyoming Whisper endpoint.
- `holidays.py` — Hebcal-backed Jewish/Israeli holidays, cached per-year on disk.

Support:
- `settings.py` — env-loaded config (paths, tokens, hosts).
- `logging_config.py` — shared logger.
- `confirmation.py` — yes/confirm flow for risky device actions.

## How to run locally

1. Copy `addon/.env.example` to `addon/.env` and fill in Anthropic + Meta + HA tokens.
2. `pip install -r addon/requirements.txt`
3. `cd addon && uvicorn app.main:app --reload --port 8000`
4. For a real WhatsApp webhook you need ngrok or a Cloudflare tunnel. Normal dev flow is to POST synthetic payloads to `/webhook` with curl.

## How to deploy

- The HA add-on at home pulls from `main`. Users update from the HA add-on Store.
- **Bump `addon/config.yaml` `version:` only at end-of-phase, not per item.**
- Between phases, `main` is expected to match the deployed version exactly. The running Zoe at home is authoritative.

## User preferences (these have burned us before)

- **Reply in English** even when the user writes Hebrew. He prefers not to context-switch between scripts inside one message.
- Messages TO the user in Hebrew must use **sender-aware gendered forms** based on facts stored in `memory` (e.g. "אתה" vs "את"). Look up the sender before composing.
- **Household vs per-sender data boundaries** (see `IMPLEMENTATION-PLAN.md` for the full split):
  - Household-wide: `anchors`, `lists`, `memory`, `expenses`, `recurring_expenses`, holidays.
  - Per-sender: `reminders`, `agenda`, `briefing`, `monitors`, `scheduled_actions`, `personal_tasks`, `conversation`.
- **Broadcast semantics**: expense-related tool outcomes go to the whole household. Everything else is private to the sender.
- **Never assume the wife is on the thread.** Always check the sender phone. If the wife hasn't messaged Zoe in the last 24h Meta's window is closed and any broadcast to her silently fails. (Item 16 adds a proactive warning; until it lands, the failure is invisible.)

## Working rules for the refactor period

Full rules in `IMPLEMENTATION-PLAN.md`. Top-line:

- One branch per item (`step-NN-slug`), one PR to `main` per item.
- `main` stays green and identical to the deployed HA add-on version until a phase is fully merged.
- Merge only after the item's acceptance criteria are met.
- **`config.yaml` version bump + HA add-on deploy happens only at end-of-phase**, never mid-item.
- Small commits inside a PR with why-messages. No big-bang commits.
- Cloud agents get the plan + a self-contained brief for one item. Do not touch other items.

## Common gotchas

- **Windows dev host**: `git push` from the Bash tool sometimes stalls on the credential helper. Fall back to PowerShell with `GIT_TERMINAL_PROMPT=0` if it hangs.
- **Anthropic credit exhaustion silently kills background loops** for a while. The signal shows on the user's Anthropic console, not in the app logs. Suspect this before chasing app-level bugs when loops stop firing.
- **Meta 24h window**: outbound to a recipient only works within 24h of their last inbound. Silent failure otherwise. See User preferences above.
- **Cloudflare Tunnel drops periodically.** The user has a watchdog that auto-restarts it. Do not chase this in application code.
- **`entities.yaml` is cached at import.** Changes require an add-on restart to take effect.
- **HA host IP is pinned to 192.168.10.150** on the HA Green (Gilat router can't do DHCP reservations). Referenced in `run.sh` and the Cloudflare Tunnel ingress config.
