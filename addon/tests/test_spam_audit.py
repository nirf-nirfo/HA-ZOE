"""Item 22: audit log store."""
import time
from unittest.mock import patch

from app import spam_audit


def test_append_and_recent(tmp_stores):
    spam_audit.append(
        uid="1", from_addr="a@a.com", subject="s",
        verdict="spam", confidence=0.95,
        action="moved_to_spam", dry_run=False,
    )
    rows = spam_audit.recent(days=1)
    assert len(rows) == 1 and rows[0].uid == "1"


def test_retention_prunes_old_entries(tmp_stores):
    """Entries older than 30 days must be dropped on next append."""
    long_ago = time.time() - 40 * 24 * 3600
    with patch("app.spam_audit.time.time", return_value=long_ago):
        spam_audit.append(
            uid="old", from_addr="x@x.com", subject="old",
            verdict="legit", confidence=0.8,
            action="left_in_inbox", dry_run=True,
        )
    spam_audit.append(
        uid="new", from_addr="y@y.com", subject="new",
        verdict="spam", confidence=0.95,
        action="moved_to_spam", dry_run=False,
    )
    rows = spam_audit.recent(days=60)  # window wide enough to see both IF present
    uids = {r.uid for r in rows}
    assert "new" in uids
    assert "old" not in uids  # pruned


def test_recent_newest_first(tmp_stores):
    spam_audit.append(uid="1", from_addr="a", subject="first",
                      verdict="spam", confidence=0.9,
                      action="moved_to_spam", dry_run=False)
    spam_audit.append(uid="2", from_addr="a", subject="second",
                      verdict="spam", confidence=0.9,
                      action="moved_to_spam", dry_run=False)
    rows = spam_audit.recent(days=1)
    assert rows[0].uid == "2"
    assert rows[1].uid == "1"
