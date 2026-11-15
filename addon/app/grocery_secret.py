"""Item 24: secret storage for the grocery-app pairing token.

The 48-char token returned by `/api/zoe/exchange` is stored in a single
on-disk JSON file under HA's /data (container-isolated). It is NEVER
echoed to logs, agent output or tool responses — the only place it
surfaces is the `Authorization: Bearer` header on outbound calls to the
grocery Worker.

Secret rotation: a successful re-pair overwrites the file. A manual
unpair clears it. The file is also the sole signal "am I paired?" —
presence = paired, absence = unpaired.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from app.settings import settings

logger = logging.getLogger(__name__)

# In-memory cache so the file is read once per process and never logged.
_cached: str | None = None
_cached_from_disk: bool = False


def _path() -> Path:
    return Path(settings.grocery_secret_path)


def _load_from_disk() -> str | None:
    p = _path()
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("grocery_secret: failed to read %s", p)
        return None
    token = data.get("token") if isinstance(data, dict) else None
    if isinstance(token, str) and token.strip():
        return token.strip()
    return None


def get_token() -> str | None:
    """Returns the paired token, or None if unpaired. Cached per process."""
    global _cached, _cached_from_disk
    if not _cached_from_disk:
        _cached = _load_from_disk()
        _cached_from_disk = True
    return _cached


def set_token(token: str) -> None:
    """Persists a newly-paired token. Overwrites any prior token (rotation)."""
    global _cached, _cached_from_disk
    if not isinstance(token, str) or not token.strip():
        raise ValueError("token must be a non-empty string")
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    # Write via a temp + rename so a crash can't leave a half-written file.
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps({"token": token}), encoding="utf-8")
    try:
        os.replace(tmp, p)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    _cached = token
    _cached_from_disk = True
    logger.info("grocery_secret: paired (token stored)")  # no token content


def clear() -> bool:
    """Removes the stored token. Returns True if a token was present."""
    global _cached, _cached_from_disk
    p = _path()
    existed = p.exists()
    if existed:
        p.unlink(missing_ok=True)
    _cached = None
    _cached_from_disk = True
    if existed:
        logger.info("grocery_secret: cleared (unpaired)")
    return existed


def is_paired() -> bool:
    return get_token() is not None


def _reset_cache_for_tests() -> None:
    """Test helper only — forces the next get_token() to re-read disk."""
    global _cached, _cached_from_disk
    _cached = None
    _cached_from_disk = False
