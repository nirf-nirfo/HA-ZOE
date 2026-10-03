"""Item 21: semantics of `_email_watch_loop`'s inner tick.

The real loop sits inside an `while True: await asyncio.sleep(60)` — a direct
await on the function would hang the test. We instead drive the single-tick
body: wait until the current UTC time, call the inner body logic by invoking
`email_watch_loop_tick`, and assert what happened.

To stay faithful to production without duplicating code, we extract one tick
by monkeypatching asyncio.sleep + letting the real coroutine run for exactly
one iteration. Each test:
  * mocks `_email_backend` (list_recent + fetch)
  * mocks `email_processor.process_email`
  * cancels the task after one iteration
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app import email_watches, loops
from app.email_backend import EmailMessage


# ------------------------------------------------------------------ helpers


def _msg(uid: str, from_addr: str = "receipt@shop.co.il",
         subject: str = "Thanks for your purchase",
         snippet: str = "total: 123") -> EmailMessage:
    return EmailMessage(
        uid=uid,
        from_addr=from_addr,
        to_addrs=["me@example.com"],
        subject=subject,
        date=datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc),
        snippet=snippet,
        body_text=f"{subject}\n{snippet}",
        attachments=[],
    )


async def _one_tick():
    """Runs exactly one iteration of `_email_watch_loop` and returns.

    We monkeypatch asyncio.sleep inside the loops module so the first
    `await asyncio.sleep(60)` returns immediately, then stop by raising a
    sentinel from the SECOND sleep (which would start the next tick).
    """
    original_sleep = asyncio.sleep
    first = {"used": False}

    class _StopLoop(Exception):
        pass

    async def _fake_sleep(_delay):
        if first["used"]:
            raise _StopLoop
        first["used"] = True
        # Give any other scheduled coroutines a chance to run.
        await original_sleep(0)

    # Patch directly on the asyncio module reference the loops module uses.
    import app.loops as loops_mod

    orig = loops_mod.asyncio.sleep
    loops_mod.asyncio.sleep = _fake_sleep
    try:
        with pytest.raises(_StopLoop):
            await loops_mod._email_watch_loop()
    finally:
        loops_mod.asyncio.sleep = orig


@pytest.fixture
def mock_backend(monkeypatch):
    backend = SimpleNamespace(
        list_recent=AsyncMock(return_value=[]),
        fetch=AsyncMock(return_value=None),
    )
    monkeypatch.setattr(loops, "_email_backend", backend)
    return backend


@pytest.fixture
def mock_processor(monkeypatch):
    proc = SimpleNamespace(process_email=MagicMock())
    monkeypatch.setattr(loops, "email_processor", proc)
    return proc


# ------------------------------------------------------------------ tests


@pytest.mark.asyncio
async def test_no_watches_is_noop(tmp_stores, mock_backend, mock_processor):
    await _one_tick()
    mock_backend.list_recent.assert_not_awaited()
    mock_processor.process_email.assert_not_called()


@pytest.mark.asyncio
async def test_due_watch_processes_match(tmp_stores, mock_backend, mock_processor):
    w = email_watches.add(name="shop", from_contains="receipt@shop.co.il")
    # Never-checked watch is always due.
    msg = _msg("42")
    mock_backend.list_recent.return_value = [msg]
    mock_backend.fetch.return_value = msg

    await _one_tick()

    mock_backend.list_recent.assert_awaited_once()
    mock_backend.fetch.assert_awaited_once_with("42")
    mock_processor.process_email.assert_called_once_with(msg)
    reloaded = email_watches.get(w.id)
    assert reloaded.last_processed_uids == ["42"]
    assert reloaded.last_checked_at is not None


@pytest.mark.asyncio
async def test_not_due_watch_skipped(tmp_stores, mock_backend, mock_processor):
    """A watch checked 5 min ago with interval_minutes=60 should NOT be touched."""
    w = email_watches.add(
        name="shop", from_contains="receipt@shop.co.il", interval_minutes=60,
    )
    email_watches.update_last_checked(
        w.id, datetime.now(timezone.utc) - timedelta(minutes=5)
    )

    await _one_tick()

    mock_backend.list_recent.assert_not_awaited()
    mock_processor.process_email.assert_not_called()


@pytest.mark.asyncio
async def test_processed_uids_not_reprocessed_across_ticks(
    tmp_stores, mock_backend, mock_processor,
):
    """The same message must only hit process_email once across repeated ticks."""
    w = email_watches.add(name="shop", from_contains="receipt@shop.co.il")
    msg = _msg("42")
    mock_backend.list_recent.return_value = [msg]
    mock_backend.fetch.return_value = msg

    # First tick: processes.
    await _one_tick()
    # Reset last_checked_at so the watch is due again on the next tick, but keep
    # last_processed_uids. In real traffic the loop clock would move forward.
    email_watches.update_last_checked(w.id, datetime(2000, 1, 1, tzinfo=timezone.utc))

    # Second tick: list_recent still returns the same uid, but it is in the
    # processed set so fetch / process_email must NOT fire again.
    await _one_tick()

    assert mock_processor.process_email.call_count == 1
    # fetch is called at most once (the first tick), never on the second.
    assert mock_backend.fetch.await_count == 1


@pytest.mark.asyncio
async def test_non_matching_message_ignored(tmp_stores, mock_backend, mock_processor):
    email_watches.add(name="shop", from_contains="receipt@shop.co.il")
    # Message from a different sender — filter does not match.
    msg = _msg("1", from_addr="newsletter@other.com")
    mock_backend.list_recent.return_value = [msg]
    mock_backend.fetch.return_value = msg

    await _one_tick()

    mock_backend.fetch.assert_not_awaited()
    mock_processor.process_email.assert_not_called()


@pytest.mark.asyncio
async def test_disabled_watch_skipped(tmp_stores, mock_backend, mock_processor):
    w = email_watches.add(name="shop", from_contains="receipt@shop.co.il")
    email_watches.disable(w.id)

    await _one_tick()

    mock_backend.list_recent.assert_not_awaited()
    mock_processor.process_email.assert_not_called()


@pytest.mark.asyncio
async def test_raising_watch_does_not_kill_loop(
    tmp_stores, mock_backend, mock_processor,
):
    """One watch's blow-up must leave later watches running on the same tick."""
    bad = email_watches.add(name="bad", from_contains="bad@x")
    good = email_watches.add(name="good", from_contains="receipt@shop.co.il")

    bad_msg = _msg("1", from_addr="bad@x")
    good_msg = _msg("2", from_addr="receipt@shop.co.il")
    mock_backend.list_recent.return_value = [bad_msg, good_msg]

    # fetch is awaited for every match, bad watch first. Explode on uid 1 only.
    async def _fetch(uid):
        if uid == "1":
            raise RuntimeError("IMAP boom")
        return good_msg

    mock_backend.fetch.side_effect = _fetch

    await _one_tick()

    # Good watch's match still got processed.
    assert mock_processor.process_email.call_count == 1
    processed_msg = mock_processor.process_email.call_args.args[0]
    assert processed_msg.uid == "2"
    # Good watch recorded its uid; bad watch did not.
    assert email_watches.get(good.id).last_processed_uids == ["2"]
    # Both watches ran (both got last_checked_at bumped).
    assert email_watches.get(good.id).last_checked_at is not None
    assert email_watches.get(bad.id).last_checked_at is not None


@pytest.mark.asyncio
async def test_processor_exception_still_marks_uid(
    tmp_stores, mock_backend, mock_processor,
):
    """A persistently bad message must not loop forever: mark it processed
    even when process_email throws."""
    w = email_watches.add(name="shop", from_contains="receipt@shop.co.il")
    msg = _msg("77")
    mock_backend.list_recent.return_value = [msg]
    mock_backend.fetch.return_value = msg
    mock_processor.process_email.side_effect = ValueError("corrupt receipt")

    await _one_tick()

    assert email_watches.get(w.id).last_processed_uids == ["77"]


@pytest.mark.asyncio
async def test_subject_filter_matches_case_insensitive(
    tmp_stores, mock_backend, mock_processor,
):
    email_watches.add(name="receipts", subject_contains="receipt")
    msg = _msg("1", subject="Your RECEIPT is ready")
    mock_backend.list_recent.return_value = [msg]
    mock_backend.fetch.return_value = msg

    await _one_tick()

    mock_processor.process_email.assert_called_once()


@pytest.mark.asyncio
async def test_processor_missing_logs_and_skips(
    tmp_stores, mock_backend, monkeypatch,
):
    """When Item 20 hasn't landed (email_processor is None), the loop must
    still tick (and bump its heartbeat) without crashing."""
    monkeypatch.setattr(loops, "email_processor", None)
    email_watches.add(name="shop", from_contains="receipt@shop.co.il")

    await _one_tick()

    # list_recent must NOT be called — nothing to do without the processor.
    mock_backend.list_recent.assert_not_awaited()


@pytest.mark.asyncio
async def test_tick_bumps_heartbeat(tmp_stores, mock_backend, mock_processor):
    from app.status import _LOOP_HEARTBEAT

    _LOOP_HEARTBEAT["email_watch"]["last_tick_at"] = 0.0

    await _one_tick()

    assert _LOOP_HEARTBEAT["email_watch"]["last_tick_at"] > 0.0


@pytest.mark.asyncio
async def test_watch_matches_helper():
    """Unit-level sanity on the matcher itself — all-AND semantics plus
    case-insensitive, empty-field-means-wildcard."""
    w = email_watches.EmailWatch(
        id="x", name="x",
        from_contains="receipt@shop",
        subject_contains="",
        body_contains="",
    )
    assert loops._watch_matches(w, _msg("1", from_addr="receipt@shop.co.il"))
    assert not loops._watch_matches(w, _msg("1", from_addr="other@x"))

    w2 = email_watches.EmailWatch(
        id="x", name="x",
        from_contains="receipt",
        subject_contains="paid",
    )
    assert loops._watch_matches(
        w2, _msg("1", from_addr="receipts@a", subject="Order paid"),
    )
    # Subject misses → fails even though from matches.
    assert not loops._watch_matches(
        w2, _msg("1", from_addr="receipts@a", subject="Order shipped"),
    )
