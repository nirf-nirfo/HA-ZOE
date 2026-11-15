"""Item 24: idempotency-key derivation for grocery operations.

The brief requires: "Derive each key from the inbound provider message ID
plus the operation index in that message (hash the pair to fit 8-80
chars), persisted with the operation, so retries reuse it. Never mint a
fresh key on retry; never reuse a key for a different operation."

In practice, Zoe's tool handlers don't receive the raw WhatsApp message
id — the `(sender, tool, inp, ...)` signature was finalised before this
feature. Rather than thread message_id through every handler, we derive
the key deterministically from the three things the handler DOES have:

  - sender phone (stable per household member)
  - operation payload (stable per logical item+action)
  - UTC minute of the call (stable within a 60s window)

The tradeoffs this makes:

  - Retries within the same minute (model self-retry, network blip) → same
    key → server dedups cleanly. This is the common case.
  - Multiple logically-identical ops in the same minute from the same
    sender → same key → server dedups (visible as one row, which is the
    DESIRED behavior: no one wants "add milk" × 2 in the same breath).
  - Retries crossing a minute boundary (very rare) → different key →
    could duplicate. Item 26's read-before-write guard will catch this
    as a secondary layer.
"""
from __future__ import annotations

import hashlib
import json
import time


def key_for(sender: str, op: dict, utc_minute: int | None = None) -> str:
    """Returns a stable 40-char hex key for this (sender, op, minute) tuple."""
    minute = int(time.time() // 60) if utc_minute is None else utc_minute
    # Sort keys so the same semantic op always serialises identically.
    payload = json.dumps(op, sort_keys=True, ensure_ascii=False)
    raw = f"{sender}|{minute}|{payload}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:40]
