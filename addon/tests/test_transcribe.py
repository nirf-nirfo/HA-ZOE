"""Item 18B: voice transcription backend router + OpenAI Whisper path.

Covers:
- `_transcribe_openai` POST shape (URL, Authorization header, multipart file,
  model/language form fields).
- `_transcribe_openai` returns None when the HTTP call raises.
- Router honors `settings.transcribe_backend` and the empty-key fallback rule:
  * backend=openai with empty key -> wyoming (warning logged).
  * backend=openai with key set    -> openai.
  * backend=wyoming (key set or not) -> wyoming.
- Router returns None without calling either backend when download fails.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import transcribe
from app.settings import settings


@pytest.fixture(autouse=True)
def _restore_settings():
    """Save/restore the transcribe-related settings fields so each test
    runs with a known baseline and never leaks into the next one."""
    saved = {
        "transcribe_backend": settings.transcribe_backend,
        "openai_api_key": settings.openai_api_key,
        "whisper_host": settings.whisper_host,
        "whisper_port": settings.whisper_port,
    }
    try:
        yield
    finally:
        for k, v in saved.items():
            setattr(settings, k, v)


async def test_transcribe_openai_posts_correct_request():
    """_transcribe_openai hits OPENAI_TRANSCRIBE_URL with Bearer auth, the
    OGG multipart file, and model=whisper-1 + language=he form fields."""
    settings.openai_api_key = "sk-test-key"

    fake_resp = MagicMock()
    fake_resp.raise_for_status = MagicMock()
    fake_resp.json = MagicMock(return_value={"text": "  שלום  "})

    post_mock = AsyncMock(return_value=fake_resp)

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            self.timeout = kwargs.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            return await post_mock(*args, **kwargs)

    with patch.object(transcribe.httpx, "AsyncClient", _FakeAsyncClient):
        result = await transcribe._transcribe_openai(b"\x00\x01OGGbytes")

    assert result == "שלום"
    post_mock.assert_awaited_once()
    args, kwargs = post_mock.call_args
    assert args[0] == transcribe.OPENAI_TRANSCRIBE_URL
    assert kwargs["headers"] == {"Authorization": "Bearer sk-test-key"}
    assert kwargs["files"] == {"file": ("voice.ogg", b"\x00\x01OGGbytes", "audio/ogg")}
    assert kwargs["data"] == {"model": "whisper-1", "language": "he"}


async def test_transcribe_openai_returns_none_on_exception():
    """Any error during the POST -> None (never raises to caller)."""
    settings.openai_api_key = "sk-test-key"

    class _BoomClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            raise RuntimeError("network down")

    with patch.object(transcribe.httpx, "AsyncClient", _BoomClient):
        result = await transcribe._transcribe_openai(b"anything")

    assert result is None


async def test_router_openai_empty_key_falls_back_to_wyoming():
    """backend=openai but no key -> warning + wyoming path (openai not awaited)."""
    settings.transcribe_backend = "openai"
    settings.openai_api_key = ""

    with (
        patch.object(transcribe, "_download_audio", AsyncMock(return_value=b"ogg")) as dl,
        patch.object(transcribe, "_transcribe_wyoming", AsyncMock(return_value="w-text")) as wyo,
        patch.object(transcribe, "_transcribe_openai", AsyncMock(return_value="o-text")) as oai,
    ):
        result = await transcribe.transcribe_audio("media-xyz")

    assert result == "w-text"
    dl.assert_awaited_once_with("media-xyz")
    wyo.assert_awaited_once_with(b"ogg")
    oai.assert_not_awaited()


async def test_router_openai_with_key_calls_openai():
    """backend=openai + key present -> openai path (wyoming not awaited)."""
    settings.transcribe_backend = "openai"
    settings.openai_api_key = "sk-live"

    with (
        patch.object(transcribe, "_download_audio", AsyncMock(return_value=b"ogg-bytes")) as dl,
        patch.object(transcribe, "_transcribe_wyoming", AsyncMock(return_value="w-text")) as wyo,
        patch.object(transcribe, "_transcribe_openai", AsyncMock(return_value="o-text")) as oai,
    ):
        result = await transcribe.transcribe_audio("media-abc")

    assert result == "o-text"
    dl.assert_awaited_once_with("media-abc")
    oai.assert_awaited_once_with(b"ogg-bytes")
    wyo.assert_not_awaited()


async def test_router_wyoming_even_with_key_set():
    """backend=wyoming explicitly chosen -> wyoming (never openai), even if a
    key happens to be set."""
    settings.transcribe_backend = "wyoming"
    settings.openai_api_key = "sk-should-be-ignored"

    with (
        patch.object(transcribe, "_download_audio", AsyncMock(return_value=b"ogg")) as dl,
        patch.object(transcribe, "_transcribe_wyoming", AsyncMock(return_value="w-text")) as wyo,
        patch.object(transcribe, "_transcribe_openai", AsyncMock(return_value="o-text")) as oai,
    ):
        result = await transcribe.transcribe_audio("media-def")

    assert result == "w-text"
    dl.assert_awaited_once()
    wyo.assert_awaited_once_with(b"ogg")
    oai.assert_not_awaited()


async def test_router_download_failure_returns_none_without_calling_backend():
    """_download_audio raising -> None and neither backend is awaited."""
    settings.transcribe_backend = "openai"
    settings.openai_api_key = "sk-live"

    with (
        patch.object(transcribe, "_download_audio", AsyncMock(side_effect=RuntimeError("meta 500"))),
        patch.object(transcribe, "_transcribe_wyoming", AsyncMock()) as wyo,
        patch.object(transcribe, "_transcribe_openai", AsyncMock()) as oai,
    ):
        result = await transcribe.transcribe_audio("media-boom")

    assert result is None
    wyo.assert_not_awaited()
    oai.assert_not_awaited()
