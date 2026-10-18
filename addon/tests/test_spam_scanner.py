"""Item 22: scanner orchestration — allowlist / memory / classifier / gating."""
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app import spam_audit, spam_scan_schedule, spam_scanner, spam_sender_memory
from app.email_backend import EmailMessage
from app.settings import settings
from app.spam_classifier import Classification, Verdict


def _hdr(uid, from_addr, subject="s"):
    return EmailMessage(
        uid=uid, from_addr=from_addr, to_addrs=[],
        subject=subject, date=datetime(2026, 10, 18, tzinfo=timezone.utc),
        snippet="", body_text="", attachments=[],
    )


def _full(uid, from_addr, subject="s", body="hello"):
    return EmailMessage(
        uid=uid, from_addr=from_addr, to_addrs=["me@z.com"],
        subject=subject, date=datetime(2026, 10, 18, tzinfo=timezone.utc),
        snippet=body[:200], body_text=body, attachments=[],
    )


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _settings_defaults(tmp_stores, monkeypatch):
    monkeypatch.setattr(settings, "email_write_enabled", False)
    monkeypatch.setattr(settings, "email_spam_folder", "", raising=False)
    monkeypatch.setattr(settings, "email_spam_allowlist", "", raising=False)
    monkeypatch.setattr(settings, "allowed_sender_numbers", "972501111111,972502222222")
    # Live mode OFF by default — dry-run is the mandatory first-week state.
    spam_scan_schedule.get_schedule()  # seed defaults
    yield


def _mk_backend(headers, full_by_uid, spam_folder="[Gmail]/Spam"):
    backend = MagicMock()
    backend.list_recent = AsyncMock(return_value=headers)
    backend.fetch = AsyncMock(side_effect=lambda uid: full_by_uid.get(uid))
    backend.detect_spam_folder = AsyncMock(return_value=spam_folder)
    backend.move_to_folder = AsyncMock(return_value=True)
    return backend


class TestAllowlist:
    def test_allowlisted_sender_skipped_no_llm(self, monkeypatch):
        monkeypatch.setattr(settings, "email_spam_allowlist", "bank.co.il")
        backend = _mk_backend([_hdr("1", "notice@bank.co.il")], {})
        classifier = MagicMock()
        outcome = _run(spam_scanner.run_scan(backend, classify_fn=classifier))
        assert outcome.allowlisted == 1
        assert outcome.classified == 0
        classifier.assert_not_called()
        backend.fetch.assert_not_called()
        backend.move_to_folder.assert_not_called()
        rows = spam_audit.recent(1)
        assert rows[0].verdict == "allowlist"
        assert rows[0].action == "left_in_inbox"


class TestSenderMemoryHit:
    def test_memory_hit_spam_moves_when_live(self, monkeypatch):
        monkeypatch.setattr(settings, "email_write_enabled", True)
        spam_scan_schedule.enable_live_mode()
        spam_sender_memory.set_verdict("promos@shop.com", "spam")
        backend = _mk_backend([_hdr("1", "promos@shop.com")], {})
        classifier = MagicMock()
        outcome = _run(spam_scanner.run_scan(backend, classify_fn=classifier))
        assert outcome.memory_hits == 1
        assert outcome.moved == 1
        classifier.assert_not_called()
        backend.move_to_folder.assert_awaited_once_with("1", "[Gmail]/Spam")
        assert spam_sender_memory.get("promos@shop.com").hits == 1

    def test_memory_hit_spam_in_dry_run_does_not_move(self):
        spam_sender_memory.set_verdict("promos@shop.com", "spam")
        backend = _mk_backend([_hdr("1", "promos@shop.com")], {})
        outcome = _run(spam_scanner.run_scan(backend, classify_fn=MagicMock()))
        assert outcome.memory_hits == 1
        assert outcome.moved == 0
        backend.move_to_folder.assert_not_called()
        rows = spam_audit.recent(1)
        assert rows[0].action == "dry_run_would_move"
        assert rows[0].dry_run is True

    def test_memory_hit_legit_leaves_in_inbox(self):
        spam_sender_memory.set_verdict("mom@family.com", "legit")
        backend = _mk_backend([_hdr("1", "mom@family.com")], {})
        outcome = _run(spam_scanner.run_scan(backend, classify_fn=MagicMock()))
        assert outcome.memory_hits == 1
        backend.move_to_folder.assert_not_called()
        rows = spam_audit.recent(1)
        assert rows[0].verdict == "memory_hit_legit"


class TestClassifierPath:
    def test_confident_spam_moves_when_live(self, monkeypatch):
        monkeypatch.setattr(settings, "email_write_enabled", True)
        spam_scan_schedule.enable_live_mode()
        headers = [_hdr("1", "unknown@weird.co")]
        full = {"1": _full("1", "unknown@weird.co", body="Win a free iPhone!")}
        backend = _mk_backend(headers, full)
        classify = MagicMock(return_value=[
            Classification(uid="1", verdict=Verdict("spam", 0.95, "promo")),
        ])
        outcome = _run(spam_scanner.run_scan(backend, classify_fn=classify))
        assert outcome.classified == 1
        assert outcome.moved == 1
        assert spam_sender_memory.get("unknown@weird.co").verdict == "spam"

    def test_confident_spam_in_dry_run_logs_only(self):
        headers = [_hdr("1", "unknown@weird.co")]
        full = {"1": _full("1", "unknown@weird.co")}
        backend = _mk_backend(headers, full)
        classify = MagicMock(return_value=[
            Classification(uid="1", verdict=Verdict("spam", 0.95, "promo")),
        ])
        outcome = _run(spam_scanner.run_scan(backend, classify_fn=classify))
        assert outcome.moved == 0
        backend.move_to_folder.assert_not_called()
        # Sender memory not updated during dry-run — the user hasn't confirmed yet.
        assert spam_sender_memory.get("unknown@weird.co") is None
        rows = spam_audit.recent(1)
        assert rows[0].action == "dry_run_would_move"

    def test_uncertain_leaves_in_inbox_no_mutation(self, monkeypatch):
        monkeypatch.setattr(settings, "email_write_enabled", True)
        spam_scan_schedule.enable_live_mode()
        headers = [_hdr("1", "mystery@q.com")]
        full = {"1": _full("1", "mystery@q.com")}
        backend = _mk_backend(headers, full)
        classify = MagicMock(return_value=[
            Classification(uid="1", verdict=Verdict("uncertain", 0.5, "could be either")),
        ])
        outcome = _run(spam_scanner.run_scan(backend, classify_fn=classify))
        assert outcome.moved == 0
        backend.move_to_folder.assert_not_called()
        rows = spam_audit.recent(1)
        assert rows[0].action == "left_in_inbox"
        assert rows[0].verdict == "uncertain"

    def test_legit_leaves_in_inbox(self, monkeypatch):
        monkeypatch.setattr(settings, "email_write_enabled", True)
        spam_scan_schedule.enable_live_mode()
        headers = [_hdr("1", "bank@legit.com")]
        full = {"1": _full("1", "bank@legit.com", body="Your statement is ready")}
        backend = _mk_backend(headers, full)
        classify = MagicMock(return_value=[
            Classification(uid="1", verdict=Verdict("legit", 0.9, "bank statement")),
        ])
        outcome = _run(spam_scanner.run_scan(backend, classify_fn=classify))
        backend.move_to_folder.assert_not_called()
        assert outcome.moved == 0


class TestHardGate:
    def test_email_write_enabled_false_overrides_schedule_live(self):
        """Even if schedule says live, write-disabled must suppress moves."""
        # write_enabled stays False (autouse default); flip schedule live.
        spam_scan_schedule.enable_live_mode()
        spam_sender_memory.set_verdict("promos@shop.com", "spam")
        backend = _mk_backend([_hdr("1", "promos@shop.com")], {})
        outcome = _run(spam_scanner.run_scan(backend, classify_fn=MagicMock()))
        assert outcome.moved == 0
        backend.move_to_folder.assert_not_called()
        assert outcome.dry_run is True


class TestMisc:
    def test_last_scan_at_updated(self):
        backend = _mk_backend([], {})
        _run(spam_scanner.run_scan(backend, classify_fn=MagicMock()))
        assert spam_scan_schedule.get_schedule().last_scan_at > 0

    def test_fetch_failure_skips_that_email(self):
        """A header for which fetch() returns None should NOT reach the
        classifier — otherwise we'd pay for an empty body classification."""
        headers = [_hdr("1", "x@y.com")]
        backend = _mk_backend(headers, {})  # fetch returns None
        classify = MagicMock(return_value=[])
        outcome = _run(spam_scanner.run_scan(backend, classify_fn=classify))
        assert outcome.classified == 0
        classify.assert_not_called()
