"""Item 22: classifier batching + response parsing."""
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

from app import spam_classifier
from app.email_backend import EmailMessage


def _msg(uid, from_addr, subject="s", body="b"):
    return EmailMessage(
        uid=uid, from_addr=from_addr, to_addrs=["household@z.com"],
        subject=subject,
        date=datetime(2026, 10, 18, tzinfo=timezone.utc),
        snippet=body[:200], body_text=body, attachments=[],
    )


def _fake_response(results):
    """Minimal Anthropic Message-shape stand-in: `.content` with one text block."""
    block = MagicMock()
    block.type = "text"
    block.text = json.dumps({"results": results})
    resp = MagicMock()
    resp.content = [block]
    return resp


def test_single_batch_classification():
    client = MagicMock()
    client.messages.create.return_value = _fake_response([
        {"index": 0, "verdict": "spam", "confidence": 0.95, "reason": "promo"},
        {"index": 1, "verdict": "legit", "confidence": 0.8, "reason": "transactional"},
    ])
    msgs = [_msg("1", "promo@x.com"), _msg("2", "bank@y.com")]
    out = spam_classifier.classify(msgs, client=client)
    assert len(out) == 2
    assert out[0].uid == "1" and out[0].verdict.verdict == "spam"
    assert out[1].uid == "2" and out[1].verdict.verdict == "legit"
    # Verify the system prompt was marked cacheable.
    call_kwargs = client.messages.create.call_args.kwargs
    assert call_kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert call_kwargs["model"] == "claude-haiku-4-5"


def test_batches_at_batch_size():
    """More than BATCH_SIZE messages → multiple API calls."""
    client = MagicMock()
    # Each call returns N results. We'll return the per-batch index each time.
    def side(**kw):
        # Count the "[i]" labels in the user prompt to know batch size.
        user = kw["messages"][0]["content"]
        n = user.count("\n[")
        return _fake_response([
            {"index": i, "verdict": "legit", "confidence": 0.7, "reason": ""}
            for i in range(n)
        ])
    client.messages.create.side_effect = side

    msgs = [_msg(str(i), "x@y.com") for i in range(spam_classifier.BATCH_SIZE + 3)]
    out = spam_classifier.classify(msgs, client=client)
    assert len(out) == len(msgs)
    # Two API calls: one full batch + one with the remaining 3.
    assert client.messages.create.call_count == 2


def test_malformed_batch_response_falls_back_to_uncertain():
    client = MagicMock()
    block = MagicMock(); block.type = "text"; block.text = "not json at all"
    resp = MagicMock(); resp.content = [block]
    client.messages.create.return_value = resp
    msgs = [_msg("1", "x@y.com")]
    out = spam_classifier.classify(msgs, client=client)
    assert out[0].verdict.verdict == "uncertain"
    assert out[0].verdict.confidence == 0.0


def test_api_exception_falls_back_to_uncertain():
    client = MagicMock()
    client.messages.create.side_effect = RuntimeError("boom")
    msgs = [_msg("1", "x@y.com"), _msg("2", "a@b.com")]
    out = spam_classifier.classify(msgs, client=client)
    assert all(c.verdict.verdict == "uncertain" for c in out)
    assert all(c.verdict.confidence == 0.0 for c in out)


def test_per_email_malformed_row_only_affects_that_one():
    client = MagicMock()
    client.messages.create.return_value = _fake_response([
        {"index": 0, "verdict": "spam", "confidence": 0.95, "reason": "promo"},
        {"foo": "bar"},  # bad row for index 1
    ])
    msgs = [_msg("1", "p@x.com"), _msg("2", "q@y.com")]
    out = spam_classifier.classify(msgs, client=client)
    assert out[0].verdict.verdict == "spam"
    assert out[1].verdict.verdict == "uncertain"  # fallback for bad row


def test_empty_input_short_circuits():
    assert spam_classifier.classify([], client=MagicMock()) == []
