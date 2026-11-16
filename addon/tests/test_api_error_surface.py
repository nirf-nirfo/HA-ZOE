"""Tests for the APIError → user message translation used when Anthropic
rejects a call (credit exhaustion, bad key, rate limit, generic). The
whole point is that the user sees SOMETHING instead of the task dying
silently — regressions here get people stuck in 'why is Zoe quiet' hell."""
import httpx
from anthropic import APIError

from app.agent_loop import _api_error_user_message


def _make(message: str) -> APIError:
    """Build an APIError the way the SDK does, without touching the network."""
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    body = {"type": "error", "error": {"type": "invalid_request_error", "message": message}}
    return APIError(message=message, request=req, body=body)


def test_credit_exhaustion_specific_wording():
    exc = _make("Your credit balance is too low to access the Anthropic API.")
    out = _api_error_user_message(exc)
    assert "Anthropic credits נגמרו" in out
    assert "Plans & Billing" in out


def test_auth_failure_specific_wording():
    exc = _make("authentication failed: invalid x-api-key")
    out = _api_error_user_message(exc)
    assert "API key" in out


def test_rate_limit_specific_wording():
    exc = _make("rate limit exceeded")
    out = _api_error_user_message(exc)
    assert "רייט" in out or "דקה" in out


def test_generic_error_still_surfaced():
    exc = _make("something weird happened")
    out = _api_error_user_message(exc)
    assert "Anthropic" in out
    assert "something weird happened" in out


def test_long_error_truncated():
    exc = _make("x" * 1000)
    out = _api_error_user_message(exc)
    # Length cap keeps the WhatsApp reply readable.
    assert len(out) < 400
