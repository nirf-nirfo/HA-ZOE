import asyncio
import subprocess
import tempfile
from pathlib import Path

import httpx
from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.client import AsyncTcpClient

from app.logging_config import logger
from app.settings import settings

GRAPH_API_BASE = "https://graph.facebook.com/v20.0"
OPENAI_TRANSCRIBE_URL = "https://api.openai.com/v1/audio/transcriptions"


async def _download_audio(media_id: str) -> bytes:
    """Fetch the audio file from WhatsApp's servers."""
    headers = {"Authorization": f"Bearer {settings.whatsapp_access_token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        # Step 1: get the download URL
        resp = await client.get(f"{GRAPH_API_BASE}/{media_id}", headers=headers)
        resp.raise_for_status()
        url = resp.json()["url"]
        # Step 2: download the file
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.content


def _convert_to_wav(audio_bytes: bytes) -> bytes:
    """Convert any audio format to 16kHz mono PCM WAV using ffmpeg."""
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as src:
        src.write(audio_bytes)
        src_path = src.name

    dst_path = src_path.replace(".ogg", ".wav")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", src_path, "-ar", "16000", "-ac", "1", "-f", "wav", dst_path],
            check=True,
            capture_output=True,
        )
        return Path(dst_path).read_bytes()
    finally:
        Path(src_path).unlink(missing_ok=True)
        Path(dst_path).unlink(missing_ok=True)


async def _transcribe_wyoming(audio_bytes: bytes) -> str | None:
    """Transcribe via the on-LAN Home Assistant Wyoming/Whisper endpoint.

    Behavior-preserving extraction of the original path: ffmpeg to 16kHz mono
    WAV, strip the 44-byte header, stream PCM over Wyoming, return the first
    Transcript event's text (stripped) or None on any error.
    """
    try:
        wav_bytes = await asyncio.to_thread(_convert_to_wav, audio_bytes)
    except Exception as exc:
        logger.error("Failed to convert audio for wyoming: %s", exc)
        return None

    try:
        async with AsyncTcpClient(settings.whisper_host, settings.whisper_port) as client:
            await client.write_event(Transcribe(language="he").event())
            await client.write_event(
                AudioStart(rate=16000, width=2, channels=1).event()
            )
            # Send in 1-second chunks (16000 samples x 2 bytes)
            chunk_size = 32000
            # Skip 44-byte WAV header
            pcm = wav_bytes[44:]
            for i in range(0, len(pcm), chunk_size):
                await client.write_event(
                    AudioChunk(
                        rate=16000, width=2, channels=1, audio=pcm[i : i + chunk_size]
                    ).event()
                )
            await client.write_event(AudioStop().event())

            while True:
                event = await client.read_event()
                if event is None:
                    break
                if Transcript.is_type(event.type):
                    text = Transcript.from_event(event).text.strip()
                    logger.info("Transcribed via wyoming: %s", text)
                    return text or None
    except Exception as exc:
        logger.error("Wyoming transcription failed: %s", exc)
        return None


async def _transcribe_openai(audio_bytes: bytes) -> str | None:
    """Transcribe via OpenAI's Whisper API.

    OpenAI accepts OGG directly, so we skip the ffmpeg WAV conversion the
    Wyoming path needs. Returns the response's `text` field stripped, or
    None on any error.
    """
    try:
        headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
        files = {"file": ("voice.ogg", audio_bytes, "audio/ogg")}
        data = {"model": "whisper-1", "language": "he"}
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                OPENAI_TRANSCRIBE_URL, headers=headers, files=files, data=data
            )
            resp.raise_for_status()
            text = (resp.json().get("text") or "").strip()
            logger.info("Transcribed via openai: %s", text)
            return text or None
    except Exception as exc:
        logger.error("OpenAI transcription failed: %s", exc)
        return None


async def transcribe_audio(media_id: str) -> str | None:
    """Download a WhatsApp voice message and return its transcript, or None on failure.

    Routes to the configured backend (`settings.transcribe_backend`). If the
    OpenAI backend is selected but no API key is set, falls back to Wyoming
    with a WARNING so a half-configured deployment keeps transcribing instead
    of silently dropping voice notes.
    """
    try:
        audio_bytes = await _download_audio(media_id)
    except Exception as exc:
        logger.error("Failed to download audio: %s", exc)
        return None

    backend = settings.transcribe_backend
    if backend == "openai" and not settings.openai_api_key:
        logger.warning(
            "transcribe_backend=openai but openai_api_key not set; falling back to wyoming"
        )
        backend = "wyoming"

    if backend == "openai":
        return await _transcribe_openai(audio_bytes)
    return await _transcribe_wyoming(audio_bytes)
