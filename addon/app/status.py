"""Item 06 split: /health and /admin/status endpoints + loop heartbeats.

Registered on the app in main.py via `app.include_router(status_router)`.
`_beat(name, ok=...)` is imported by every background loop in loops.py
(also re-exported from main.py so old tests reaching in via app.main._beat
still resolve).
"""
import asyncio
import time
from pathlib import Path

import httpx
import yaml
from fastapi import APIRouter, Depends, HTTPException, Request

from app import (
    anchors, briefing, check_ins, expenses, inbound_tracker, monitors,
    personal_tasks, recurring_expenses, scheduled_actions, senders,
)
from app import reminders as reminders_mod
from app.settings import settings


# --- Item 07: health/status tracking --------------------------------------
# Process-wide start time and per-loop heartbeats consumed by /health and
# /admin/status. `_beat(name, ok=...)` is called by each background loop at
# the top of every tick; anything that raises inside a loop bumps
# consecutive_errors, and a healthy tick resets it to zero.
_STARTUP_TS: float = time.time()
_LOOP_NAMES = (
    "reminder", "monitor", "scheduled_action",
    "daily_briefing", "check_in", "meta_window",
)
_LOOP_HEARTBEAT: dict[str, dict[str, float | int]] = {
    name: {"last_tick_at": 0.0, "consecutive_errors": 0} for name in _LOOP_NAMES
}


def _beat(name: str, ok: bool = True) -> None:
    """Records a loop tick. Never raises — a broken heartbeat must not break its loop."""
    try:
        entry = _LOOP_HEARTBEAT.get(name)
        if entry is None:
            return
        entry["last_tick_at"] = time.time()
        if ok:
            entry["consecutive_errors"] = 0
        else:
            entry["consecutive_errors"] = int(entry["consecutive_errors"]) + 1
    except Exception:
        pass


def _load_version() -> str:
    """Reads the deployed version from addon/config.yaml once at import time."""
    try:
        cfg_path = Path(__file__).parent.parent / "config.yaml"
        with cfg_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return str(data.get("version", "unknown"))
    except Exception:
        return "unknown"


_VERSION = _load_version()

# Cached reachability probes. Anthropic list() and HA HEAD are cheap but not
# free; /health is likely to be polled by an HA dashboard card, so cache both.
_reach_cache: dict[str, tuple[float, bool]] = {}
_ANTHROPIC_TTL = 60.0
_HA_TTL = 30.0


async def _check_anthropic_reachable() -> bool:
    now = time.time()
    ts, cached = _reach_cache.get("anthropic", (0.0, False))
    if ts and now - ts < _ANTHROPIC_TTL:
        return cached
    from app.claude_agent import _client  # local import: avoid cycle at module load
    try:
        await asyncio.wait_for(
            asyncio.to_thread(lambda: _client.models.list()), timeout=3.0
        )
        ok = True
    except Exception:
        ok = False
    _reach_cache["anthropic"] = (now, ok)
    return ok


async def _check_ha_reachable() -> bool:
    now = time.time()
    ts, cached = _reach_cache.get("ha", (0.0, False))
    if ts and now - ts < _HA_TTL:
        return cached
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.head(f"{settings.ha_base_url.rstrip('/')}/api/")
        ok = resp.status_code < 500
    except Exception:
        ok = False
    _reach_cache["ha"] = (now, ok)
    return ok


async def _health_payload() -> dict:
    anthropic_ok = await _check_anthropic_reachable()
    ha_ok = await _check_ha_reachable()
    loops_unhealthy = any(
        int(v["consecutive_errors"]) > 3 for v in _LOOP_HEARTBEAT.values()
    )
    status = "ok" if (anthropic_ok and ha_ok and not loops_unhealthy) else "degraded"
    return {
        "status": status,
        "version": _VERSION,
        "uptime_seconds": int(time.time() - _STARTUP_TS),
        "anthropic_reachable": anthropic_ok,
        "ha_reachable": ha_ok,
        "loops": {k: dict(v) for k, v in _LOOP_HEARTBEAT.items()},
    }


def _require_lan(request: Request) -> None:
    """FastAPI dep: allow /admin/status only from RFC1918 or loopback callers.
    Cheaper than middleware for a single route and keeps the check colocated."""
    host = request.client.host if request.client else ""
    if not (host.startswith("192.168.") or host.startswith("127.") or host.startswith("10.")):
        raise HTTPException(status_code=403, detail="LAN only")


def _allowed_senders() -> set[str]:
    return {n.strip() for n in settings.allowed_sender_numbers.split(",") if n.strip()}


status_router = APIRouter()


@status_router.get("/health")
async def health() -> dict:
    """Unauthenticated, high-level liveness/degradation snapshot. No secrets."""
    return await _health_payload()


@status_router.get("/admin/status", dependencies=[Depends(_require_lan)])
async def admin_status() -> dict:
    """LAN-only. Richer inspection of every store — counts and single summary
    fields only, never full objects. Safe to bookmark from a browser on the
    home network; a public reverse proxy must not be pointed at this route."""
    now = time.time()
    entries = inbound_tracker.all_senders()
    meta_window = {
        sender: {
            "last_inbound_hours_ago": round((now - e.last_inbound_at) / 3600, 2),
            "warned_23h": e.warned_23h,
            "name": senders.resolve(sender),
        }
        for sender, e in entries.items()
    }
    return {
        "health": await _health_payload(),
        "stores": {
            "reminders": {
                "count": reminders_mod.count_all(),
                "next_fire_at": reminders_mod.next_fire_at(),
            },
            "check_ins": {
                "count": check_ins.count_all(),
                "next_fire_at": check_ins.next_fire_at(),
            },
            "expenses": {
                "count": expenses.count_all(),
                "total_this_month": expenses.summary(period="this_month")["total"],
            },
            "anchors": {"count": len(anchors.list_all())},
            "personal_tasks": {"count_by_sender": personal_tasks.count_by_sender()},
            "recurring_expenses": {"count": len(recurring_expenses.list_all())},
            "monitors": {"count": monitors.count_active()},
            "scheduled_actions": {
                "count": scheduled_actions.count_pending(),
                "next_fire_at": scheduled_actions.next_fire_at(),
            },
            "briefing": {"configured_senders": len(briefing.all_configs())},
        },
        "senders": {
            "allowed": sorted(_allowed_senders()),
            "meta_window": meta_window,
        },
    }
