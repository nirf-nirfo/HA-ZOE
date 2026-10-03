"""Test fixtures.

`tmp_stores` repoints every `settings.*_path` at a temp dir so each test
gets isolated JSON files and never touches the real /data/*.json.
"""
import pytest

from app.settings import settings

# All Settings fields whose name ends in `_path` — matches the audit in
# `addon/app/settings.py`. If a new store is added later, appending its
# `_path` field name here is the only change needed.
_STORE_PATH_FIELDS = [
    "reminders_path",
    "lists_path",
    "memory_path",
    "monitors_path",
    "scheduled_actions_path",
    "agenda_path",
    "briefing_config_path",
    "anchors_path",
    "holidays_cache_path",
    "conversation_path",
    "conversation_log_path",
    "expenses_path",
    "recurring_expenses_path",
    "check_ins_path",
    "personal_tasks_path",
    "email_watches_path",
]


@pytest.fixture
def tmp_stores(tmp_path, monkeypatch):
    """Per-test temp dir with every `settings.*_path` repointed inside it.

    Yields the tmp dir so a test can inspect / mutate the on-disk JSON.
    """
    for field in _STORE_PATH_FIELDS:
        monkeypatch.setattr(settings, field, str(tmp_path / f"{field}.json"))
    return tmp_path
