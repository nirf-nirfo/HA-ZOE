"""Fixtures for the behavior harness.

The harness makes real Anthropic API calls, so it is gated behind the
``ANTHROPIC_API_KEY_TEST`` env var — CI never runs it automatically. If the
var is not set, every behavior test is skipped with a clear message.

We deliberately require a SEPARATE env var (`ANTHROPIC_API_KEY_TEST`) rather
than reusing `ANTHROPIC_API_KEY`. That keeps a developer's normal add-on key
from being spent by an accidental `pytest` invocation.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session")
def behavior_api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY_TEST")
    if not key:
        pytest.skip(
            "Behavior harness disabled: set ANTHROPIC_API_KEY_TEST to run. "
            "See addon/tests/behavior/README.md for cost estimates."
        )
    return key


@pytest.fixture(scope="session", autouse=True)
def _wire_anthropic_key(behavior_api_key: str, monkeypatch_session):
    """Point ``settings.anthropic_api_key`` (and the env var) at the test key.

    The claude_agent module reads settings.anthropic_api_key at import time to
    build its ``Anthropic`` client, so we set the env var BEFORE importing it.
    """
    monkeypatch_session.setenv("ANTHROPIC_API_KEY", behavior_api_key)


@pytest.fixture(scope="session")
def monkeypatch_session():
    """A session-scoped monkeypatch (pytest's built-in is function-scoped)."""
    from _pytest.monkeypatch import MonkeyPatch

    mp = MonkeyPatch()
    yield mp
    mp.undo()
