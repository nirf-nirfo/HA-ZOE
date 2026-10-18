"""Item 22: learned per-sender verdicts for the spam scanner.

The biggest cost lever in the classifier design: after the user has
confirmed a verdict (or an auto-move has gone through), remember it. Next
time an email arrives from the same sender, the scanner looks up the
verdict in O(1) and skips the LLM call.

Keys are normalized sender addresses (lowercased, stripped). Values are
simple records with the verdict, when it was learned, and how many
messages have been matched since.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Literal

from app._store import Store
from app.settings import settings

Verdict = Literal["spam", "legit"]


@dataclass
class SenderVerdict:
    verdict: Verdict
    learned_at: float
    hits: int = 0


_store: Store = Store(lambda: settings.spam_sender_memory_path)


def _normalize(addr: str) -> str:
    return (addr or "").strip().lower()


def _load() -> dict[str, SenderVerdict]:
    raw = _store.load_dict() or {}
    out: dict[str, SenderVerdict] = {}
    for k, v in raw.items():
        if not isinstance(v, dict):
            continue
        try:
            out[k] = SenderVerdict(
                verdict=v["verdict"],
                learned_at=float(v.get("learned_at", 0.0)),
                hits=int(v.get("hits", 0)),
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _save(d: dict[str, SenderVerdict]) -> None:
    _store.save_dict({k: asdict(v) for k, v in d.items()})


def set_verdict(addr: str, verdict: Verdict) -> SenderVerdict:
    """Overwrites any prior verdict for the sender."""
    key = _normalize(addr)
    data = _load()
    rec = SenderVerdict(verdict=verdict, learned_at=time.time(), hits=0)
    data[key] = rec
    _save(data)
    return rec


def get(addr: str) -> SenderVerdict | None:
    return _load().get(_normalize(addr))


def record_hit(addr: str) -> None:
    """Bumps the hit counter for a sender we already know. Used after the
    scanner skips the LLM because of a memory match."""
    key = _normalize(addr)
    data = _load()
    rec = data.get(key)
    if rec is None:
        return
    data[key] = SenderVerdict(
        verdict=rec.verdict, learned_at=rec.learned_at, hits=rec.hits + 1
    )
    _save(data)


def forget(addr: str) -> bool:
    """Removes a verdict; returns True if there was one."""
    key = _normalize(addr)
    data = _load()
    if key in data:
        del data[key]
        _save(data)
        return True
    return False


def all_verdicts() -> dict[str, SenderVerdict]:
    return _load()


def count() -> int:
    return len(_load())
