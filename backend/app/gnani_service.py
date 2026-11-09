"""
Gnani AI Service Provider Adapter for BharatVoice AI.
Exclusively integrates Gnani Prisma STT and Gnani Timbre TTS for all voice operations.
"""
import os
import io
import json
import logging
from typing import Optional, Dict, Any
import httpx
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(BASE_DIR)
ROOT_DIR = os.path.dirname(BACKEND_DIR)
load_dotenv(os.path.join(BACKEND_DIR, ".env"))
load_dotenv(os.path.join(ROOT_DIR, ".env"))
load_dotenv()

logger = logging.getLogger(__name__)

def get_gnani_api_key() -> str:
    return os.environ.get("GNANI_API_KEY", "").strip()

def get_gnani_access_key() -> str:
    return os.environ.get("GNANI_ACCESS_KEY", "").strip()

def get_gnani_stt_url() -> str:
    return os.environ.get("GNANI_STT_URL", "https://api.vachana.ai/stt/v3").strip()

def get_gnani_tts_url() -> str:
    return os.environ.get("GNANI_TTS_URL", "https://api.vachana.ai/api/v1/tts/inference").strip()

GNANI_API_KEY = get_gnani_api_key()
GNANI_ACCESS_KEY = get_gnani_access_key()
GNANI_STT_URL = get_gnani_stt_url()
GNANI_TTS_URL = get_gnani_tts_url()


def is_gnani_configured() -> bool:
    """Checks whether real Gnani API credentials are set in environment."""
    return bool(get_gnani_api_key() or get_gnani_access_key())


async def transcribe_speech(
    audio_bytes: bytes,
    filename: str = "audio.wav",
    content_type: str = "audio/wav",
    language_code: str = "en-IN",
) -> Dict[str, Any]:
    """
    Transcribes audio exclusively using Gnani Prisma STT.
    Supported languages include: en-IN, hi-IN, ta-IN, te-IN, kn-IN, mr-IN, etc.
    """
    if not audio_bytes or len(audio_bytes) == 0:
        raise ValueError("Audio content is empty.")

    if is_gnani_configured():
        api_key = get_gnani_api_key()
        access_key = get_gnani_access_key()
        key_to_use = api_key or access_key
        stt_url = get_gnani_stt_url()
        headers = {
            "X-API-Key-ID": key_to_use,
            "Authorization": f"Bearer {key_to_use}",
            "x-api-key": key_to_use,
        }
        files = {"audio_file": (filename, audio_bytes, content_type)}
        data = {"language_code": language_code, "model": "prisma-v2.5"}

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.post(stt_url, headers=headers, files=files, data=data)
                if res.status_code == 200:
                    resp_json = res.json()
                    transcript = resp_json.get("transcript") or resp_json.get("text") or ""
                    return {
                        "transcript": transcript.strip(),
                        "confidence": resp_json.get("confidence", 0.95),
                        "provider": "gnani-prisma",
                        "language": language_code,
                        "success": bool(transcript.strip()),
                    }
                else:
                    logger.warning("Gnani STT API returned status %d: %s", res.status_code, res.text)
        except Exception as exc:
            logger.error("Failed to connect to Gnani STT API: %s", exc)

    # When Gnani credentials are not set or API is unreachable
    return {
        "transcript": "",
        "confidence": 0.0,
        "provider": "gnani-prisma",
        "language": language_code,
        "success": False,
        "message": "Gnani credentials not configured. Set GNANI_API_KEY in environment to enable live speech-to-text.",
    }


async def synthesize_speech(
    text: str,
    language_code: str = "en-IN",
    voice_gender: str = "female",
) -> Dict[str, Any]:
    """
    Synthesizes speech exclusively using Gnani Timbre TTS.
    Returns audio WAV bytes encoded as base64 string.
    """
    if not text.strip():
        raise ValueError("Text to synthesize is empty.")

    if is_gnani_configured():
        api_key = get_gnani_api_key()
        access_key = get_gnani_access_key()
        key_to_use = api_key or access_key
        tts_url = get_gnani_tts_url()
        headers = {
            "X-API-Key-ID": key_to_use,
            "Authorization": f"Bearer {key_to_use}",
            "x-api-key": key_to_use,
            "Content-Type": "application/json",
        }
        voice_map = {
            "female": "Nalini",
            "male": "Rohit",
        }
        voice_name = voice_map.get(voice_gender, "Nalini")
        payload = {
            "text": text,
            "voice": voice_name,
            "model": "timbre-v2.5",
            "language": language_code,
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.post(tts_url, headers=headers, json=payload)
                if res.status_code == 200:
                    import base64
                    import io
                    import wave

                    content = res.content
                    if not content or len(content) == 0:
                        raise ValueError("Empty audio content received from Gnani.")

                    # If already WAV (starts with RIFF), use directly; otherwise package raw 48kHz PCM into WAV container
                    if content.startswith(b"RIFF"):
                        wav_bytes = content
                    else:
                        buf = io.BytesIO()
                        with wave.open(buf, "wb") as wf:
                            wf.setnchannels(1)       # Mono
                            wf.setsampwidth(2)      # 16-bit
                            wf.setframerate(48000)  # 48 kHz (Gnani Timbre v2.5 standard)
                            wf.writeframes(content)
                        wav_bytes = buf.getvalue()

                    audio_b64 = base64.b64encode(wav_bytes).decode("ascii")
                    return {
                        "success": True,
                        "audio_base64": audio_b64,
                        "format": "audio/wav",
                        "provider": "gnani-timbre",
                    }
                else:
                    logger.warning("Gnani TTS API returned status %d", res.status_code)
        except Exception as exc:
            logger.error("Failed to connect to Gnani TTS API: %s", exc)

    return {
        "success": False,
        "audio_base64": None,
        "format": None,
        "provider": "gnani-timbre",
        "message": "Gnani credentials not configured or unreachable. Set GNANI_API_KEY in environment for Timbre TTS.",
    }

