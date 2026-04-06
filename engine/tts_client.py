# -*- coding: utf-8 -*-
"""Async ElevenLabs TTS client with SOCKS proxy support."""

import asyncio
import logging
import time

import aiohttp
from aiohttp_socks import ProxyConnector

from bot.config import settings
from db.database import log_api_call, fire

logger = logging.getLogger(__name__)

ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128"


def _make_connector():
    """Create a proxy connector if proxy is configured."""
    if settings.elevenlabs_proxy:
        proxy_url = settings.elevenlabs_proxy.replace("socks5h://", "socks5://")
        return ProxyConnector.from_url(proxy_url, rdns=True)
    return None


async def synthesize_batch(
    segments: list[dict],
    max_concurrent: int = 3,
    on_progress: callable = None,
    story_id: int = None,
) -> list[bytes]:
    """Synthesize multiple segments with a shared session and concurrency limit.

    Each segment dict must have: text, voice_id, stability, similarity, style.
    Returns list of MP3 bytes in same order (None for failures).
    """
    semaphore = asyncio.Semaphore(max_concurrent)
    results: list[bytes | None] = [None] * len(segments)
    done_count = 0
    done_lock = asyncio.Lock()

    headers = {
        "xi-api-key": settings.elevenlabs_api_key,
        "Content-Type": "application/json",
    }

    async def _do(session: aiohttp.ClientSession, index: int, seg: dict):
        nonlocal done_count
        url = ELEVENLABS_TTS_URL.format(voice_id=seg["voice_id"])
        from db.config_manager import cfg
        tts_model = await cfg.get("model.tts", "eleven_v3")
        tts_lang = await cfg.get("tts.language_code", "ru")
        def_stab = await cfg.get("tts.default_stability", 0.45)
        def_sim = await cfg.get("tts.default_similarity", 0.80)
        def_style = await cfg.get("tts.default_style", 0.25)

        payload = {
            "text": seg["text"],
            "model_id": tts_model,
            "language_code": tts_lang,
            "voice_settings": {
                "stability": seg.get("stability", def_stab),
                "similarity_boost": seg.get("similarity", def_sim),
                "style": seg.get("style", def_style),
            },
        }

        for attempt in range(1, 4):
            t0 = time.time()
            try:
                async with semaphore:
                    async with session.post(
                        url, json=payload, headers=headers,
                        timeout=aiohttp.ClientTimeout(total=120),
                    ) as resp:
                        duration_ms = int((time.time() - t0) * 1000)
                        if resp.status == 200:
                            audio = await resp.read()
                            if len(audio) > 500:
                                results[index] = audio
                                fire(log_api_call(story_id=story_id, service="elevenlabs",
                                                  model="eleven_v3", purpose="tts", status="success",
                                                  duration_ms=duration_ms, request_text=seg["text"][:1000],
                                                  input_chars=len(seg["text"])))
                                async with done_lock:
                                    done_count += 1
                                    if on_progress:
                                        await on_progress(done_count, len(segments))
                                return
                            logger.warning("TTS audio too small: %d bytes (seg %d)", len(audio), index)
                        else:
                            body = await resp.text()
                            logger.warning("TTS HTTP %d (seg %d, attempt %d): %s", resp.status, index, attempt, body[:200])
                            fire(log_api_call(story_id=story_id, service="elevenlabs",
                                              model="eleven_v3", purpose="tts", status="failed",
                                              duration_ms=duration_ms, request_text=seg["text"][:1000],
                                              input_chars=len(seg["text"]), error=body[:500]))
                            if resp.status == 401 and "quota_exceeded" in body:
                                return
            except Exception as e:
                duration_ms = int((time.time() - t0) * 1000)
                logger.warning("TTS error (seg %d, attempt %d): %s", index, attempt, e)
                fire(log_api_call(story_id=story_id, service="elevenlabs",
                                  model="eleven_v3", purpose="tts", status="failed",
                                  duration_ms=duration_ms, error=str(e)[:500]))

            if attempt < 3:
                await asyncio.sleep(attempt * 2)

    # Single shared session for all requests
    connector = _make_connector()
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [_do(session, i, seg) for i, seg in enumerate(segments)]
        await asyncio.gather(*tasks, return_exceptions=True)

    # Check for failures
    failed = [i for i, r in enumerate(results) if r is None]
    if len(failed) > len(segments) * 0.3:
        raise RuntimeError(f"Too many TTS failures: {len(failed)}/{len(segments)}")

    return results
