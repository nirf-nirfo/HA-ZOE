"""Item 22: spam/legit classifier for the scanner.

Design notes:
  - Model: Haiku 4.5 — cheap, plenty for "is this spam from subject+sender".
    Overridable via `settings.email_spam_classifier_model`.
  - Prompt caching on the fixed system prompt (shared across every batch).
  - Batched: classify up to BATCH_SIZE emails per API call, with ONE shared
    system prompt amortized across the batch.
  - Minimal input: sender + subject + first ~200 chars of body. Keeps the
    per-message input cost low and avoids feeding the model long marketing
    HTML it doesn't need to decide.
  - Output: strict JSON schema per email, parsed into Verdict dataclasses.
    A malformed response for one email doesn't poison the batch — that one
    falls back to "uncertain" and the rest are kept.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Literal

from app.email_backend import EmailMessage
from app.settings import settings

logger = logging.getLogger(__name__)

VerdictLabel = Literal["spam", "legit", "uncertain"]

BATCH_SIZE = 10
_SNIPPET_LEN = 200
_DEFAULT_MODEL = "claude-haiku-4-5"
_MAX_TOKENS = 2048

_SYSTEM_PROMPT = (
    "You are a spam classifier for a small household's shared inbox. "
    "For EACH input email, output one JSON object with keys: "
    "`index` (integer, matches the input), `verdict` (one of \"spam\", "
    "\"legit\", \"uncertain\"), `confidence` (float 0.0-1.0), and `reason` "
    "(short string, <=200 chars, Hebrew or English, explaining your call). "
    "Return a single JSON object with the key `results` whose value is an "
    "array of per-email verdicts in input order. No markdown, no prose "
    "outside the JSON. "
    "Spam = unsolicited promotion, cold outreach, mailing lists the user "
    "didn't personally subscribe to, phishing, scams. Legit = personal, "
    "transactional (receipts, shipping, bank), expected service email the "
    "user has an account with. Uncertain = you cannot confidently tell. "
    "Be conservative on confidence: a safe default when a sender is unknown "
    "but the content looks transactional is `legit` at ~0.6, not spam. "
    "The household speaks Hebrew and English — treat both as first-class."
)


@dataclass
class Verdict:
    verdict: VerdictLabel
    confidence: float
    reason: str


@dataclass
class Classification:
    uid: str
    verdict: Verdict


def _snippet(msg: EmailMessage) -> str:
    body = (msg.body_text or msg.snippet or "").strip()
    return body[:_SNIPPET_LEN]


def _format_batch_user_prompt(batch: list[EmailMessage]) -> str:
    """Compact serialization of the batch for the model. One line per email
    to keep per-token footprint tight."""
    lines = ["Classify the following emails. Reply with ONE JSON object."]
    for i, msg in enumerate(batch):
        lines.append(
            f"\n[{i}] from={msg.from_addr!r} subject={msg.subject!r}\n"
            f"    body={_snippet(msg)!r}"
        )
    return "\n".join(lines)


def _parse_response(raw_text: str, batch_size: int) -> list[Verdict]:
    """Pull `results` out of the model's JSON. Any missing/malformed per-email
    row falls back to an uncertain@0.0 so the scanner won't move anything."""
    fallback = [Verdict("uncertain", 0.0, "parse_failure") for _ in range(batch_size)]
    try:
        text = raw_text.strip()
        # Strip accidental markdown fences in case the model slips.
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:].lstrip()
        parsed = json.loads(text)
    except (json.JSONDecodeError, AttributeError):
        logger.warning("Spam classifier: unparseable response: %s", raw_text[:200])
        return fallback
    results = parsed.get("results") if isinstance(parsed, dict) else None
    if not isinstance(results, list):
        logger.warning("Spam classifier: no 'results' array in response")
        return fallback
    out = list(fallback)
    for row in results:
        if not isinstance(row, dict):
            continue
        try:
            idx = int(row.get("index"))
        except (TypeError, ValueError):
            continue
        if idx < 0 or idx >= batch_size:
            continue
        verdict = row.get("verdict", "uncertain")
        if verdict not in ("spam", "legit", "uncertain"):
            verdict = "uncertain"
        try:
            conf = float(row.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        conf = max(0.0, min(1.0, conf))
        reason = str(row.get("reason", ""))[:300]
        out[idx] = Verdict(verdict, conf, reason)
    return out


def _run_batch(
    client,
    batch: list[EmailMessage],
    model: str,
) -> list[Verdict]:
    """Single API call for one batch. The system prompt is marked
    `cache_control: ephemeral` so repeated scans amortize it."""
    resp = client.messages.create(
        model=model,
        max_tokens=_MAX_TOKENS,
        system=[
            {
                "type": "text",
                "text": _SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {"role": "user", "content": _format_batch_user_prompt(batch)}
        ],
    )
    text = "".join(
        b.text for b in resp.content if getattr(b, "type", None) == "text"
    )
    return _parse_response(text, len(batch))


def classify(messages: list[EmailMessage], client=None) -> list[Classification]:
    """Classifies a list of emails. Batches internally; one API call per
    BATCH_SIZE messages. On total failure of a batch, those messages come
    back as uncertain@0.0 (safe default — the scanner won't act on them).

    `client` is injectable for tests; the production call uses the module-
    scope Anthropic client from `claude_agent`.
    """
    if not messages:
        return []
    if client is None:
        from app.claude_agent import _client as client  # lazy to avoid import cycle
    model = (
        getattr(settings, "email_spam_classifier_model", "") or _DEFAULT_MODEL
    )
    results: list[Classification] = []
    for i in range(0, len(messages), BATCH_SIZE):
        batch = messages[i : i + BATCH_SIZE]
        try:
            verdicts = _run_batch(client, batch, model)
        except Exception:
            logger.exception("Spam classifier: batch %d-%d failed", i, i + len(batch))
            verdicts = [
                Verdict("uncertain", 0.0, "classifier_exception")
                for _ in batch
            ]
        for msg, verdict in zip(batch, verdicts):
            results.append(Classification(uid=msg.uid, verdict=verdict))
    return results
