# -*- coding: utf-8 -*-
"""Voice message transcription via OpenRouter (Gemini with audio input)."""

import asyncio
import base64
import logging
import tempfile
import time
from pathlib import Path

import aiohttp

from bot.config import settings
from db.database import log_api_call, fire

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


async def transcribe_voice(ogg_data: bytes) -> str:
    """Transcribe voice message via OpenRouter using Gemini's audio capabilities.

    Converts OGG to MP3, encodes as base64, sends to Gemini via OpenRouter.

    Args:
        ogg_data: Raw OGG audio bytes from Telegram voice message.

    Returns:
        Transcribed text string.
    """
    # Convert OGG to MP3 via ffmpeg
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as ogg_file:
        ogg_file.write(ogg_data)
        ogg_path = Path(ogg_file.name)

    mp3_path = ogg_path.with_suffix(".mp3")

    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", str(ogg_path),
            "-ar", "16000", "-ac", "1", "-b:a", "64k",
            str(mp3_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

        if not mp3_path.exists() or mp3_path.stat().st_size < 100:
            raise RuntimeError("ffmpeg OGG->MP3 conversion failed")

        mp3_bytes = mp3_path.read_bytes()
        audio_b64 = base64.b64encode(mp3_bytes).decode("ascii")

        # Send to OpenRouter with Gemini model that supports audio
        headers = {
            "Authorization": f"Bearer {settings.openrouter_api_key}",
            "Content-Type": "application/json",
        }

        from db.config_manager import cfg
        transcribe_model = await cfg.get("model.transcribe", "google/gemini-2.5-flash")
        transcribe_prompt = await cfg.get("prompt.transcription",
            "Расшифруй это голосовое сообщение на русском языке. Это запрос на создание детской сказки — особенно внимательно расшифруй имена детей, возраст и названия. Верни ТОЛЬКО точный текст расшифровки, без комментариев и пояснений.")
        transcribe_tokens = await cfg.get("llm.transcribe_max_tokens", 500)
        transcribe_temp = await cfg.get("llm.transcribe_temperature", 0.1)

        payload = {
            "model": transcribe_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": audio_b64,
                                "format": "mp3",
                            },
                        },
                        {
                            "type": "text",
                            "text": transcribe_prompt,
                        },
                    ],
                }
            ],
            "max_tokens": transcribe_tokens,
            "temperature": transcribe_temp,
        }

        t0 = time.time()
        async with aiohttp.ClientSession() as session:
            async with session.post(
                OPENROUTER_URL, json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                duration_ms = int((time.time() - t0) * 1000)
                if resp.status != 200:
                    body = await resp.text()
                    fire(log_api_call(service="openrouter", model="google/gemini-2.5-flash",
                                      purpose="transcribe", status="failed", duration_ms=duration_ms,
                                      error=body[:500]))
                    raise RuntimeError(f"OpenRouter STT error {resp.status}: {body[:200]}")

                result = await resp.json()
                text = result["choices"][0]["message"]["content"].strip()
                usage = result.get("usage", {})

                if not text:
                    raise RuntimeError("Empty transcription")

                fire(log_api_call(service="openrouter", model="google/gemini-2.5-flash",
                                  purpose="transcribe", status="success", duration_ms=duration_ms,
                                  response_text=text[:1000],
                                  tokens_in=usage.get("prompt_tokens"),
                                  tokens_out=usage.get("completion_tokens")))

                logger.info("Transcribed voice: '%s'", text[:100])
                return text

    finally:
        ogg_path.unlink(missing_ok=True)
        mp3_path.unlink(missing_ok=True)
