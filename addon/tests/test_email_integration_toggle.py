"""Tests for the email_integration_enabled hibernation switch.

The flag is a single point of control for the email domain (Items 19-22):
when off, the IMAP/watch/spam tools are dropped from the LLM toolkit AND the
matching background loops are skipped at spawn time. Stored data is untouched
so flipping back on resumes cleanly.
"""
from app import claude_agent
from app.settings import settings


def test_tools_include_email_family_by_default(monkeypatch):
    monkeypatch.setattr(settings, "email_integration_enabled", True)
    tools = claude_agent._build_cached_tools()
    names = {t["name"] for t in tools}
    # Representative members of each family stay in.
    assert "list_recent_emails" in names
    assert "add_email_watch" in names
    assert "set_spam_scan_schedule" in names


def test_tools_drop_email_family_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "email_integration_enabled", False)
    tools = claude_agent._build_cached_tools()
    names = {t["name"] for t in tools}
    # All three families are hidden.
    for n in claude_agent.EMAIL_TOOLS:
        assert n not in names, f"{n} should be hidden when email integration is off"
    for n in claude_agent.EMAIL_WATCH_TOOLS:
        assert n not in names
    for n in claude_agent.SPAM_TOOLS:
        assert n not in names
    # Non-email tools stay in — representative check so a future accidental
    # over-broad filter is caught.
    assert "add_to_list" in names
    assert "add_expense" in names
    assert "set_reminder" in names


def test_hidden_tool_count_matches_the_three_sets(monkeypatch):
    monkeypatch.setattr(settings, "email_integration_enabled", True)
    on = {t["name"] for t in claude_agent._build_cached_tools()}
    monkeypatch.setattr(settings, "email_integration_enabled", False)
    off = {t["name"] for t in claude_agent._build_cached_tools()}
    removed = on - off
    expected = claude_agent.EMAIL_TOOLS | claude_agent.EMAIL_WATCH_TOOLS | claude_agent.SPAM_TOOLS
    assert removed == expected
