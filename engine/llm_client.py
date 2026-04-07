# -*- coding: utf-8 -*-
"""Async LLM client via OpenRouter (Gemini 2.5 Pro)."""

import json
import logging
import re
import time

import aiohttp

from bot.config import settings
from engine.story_parser import SCREENWRITER_PROMPT
from db.database import log_api_call, fire

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


async def _call_llm(system: str, user: str, max_retries: int = 3,
                    story_id: int = None, purpose: str = "llm",
                    temperature: float = None, max_tokens: int = None) -> str:
    """Call OpenRouter API and return the assistant's text response."""
    from db.config_manager import cfg
    model = await cfg.get("model.llm", settings.llm_model)
    temp = temperature if temperature is not None else await cfg.get("llm.screenplay_temperature", 0.8)
    tokens = max_tokens if max_tokens is not None else await cfg.get("llm.screenplay_max_tokens", 8000)

    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temp,
        "max_tokens": tokens,
    }

    from engine.http_session import get_session

    for attempt in range(1, max_retries + 1):
        t0 = time.time()
        try:
            session = get_session()
            async with session.post(OPENROUTER_URL, json=payload, headers=headers) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        duration_ms = int((time.time() - t0) * 1000)
                        logger.warning("LLM HTTP %d (attempt %d): %s", resp.status, attempt, body[:300])
                        fire(log_api_call(story_id=story_id, service="openrouter", model=model,
                                          purpose=purpose, status="failed", duration_ms=duration_ms,
                                          request_text=user[:10000], error=body[:1000]))
                        continue
                    data = await resp.json()
                    duration_ms = int((time.time() - t0) * 1000)
                    content = data["choices"][0]["message"]["content"]
                    usage = data.get("usage", {})
                    if not content or not content.strip():
                        logger.warning("LLM returned empty content (attempt %d)", attempt)
                        continue
                    fire(log_api_call(story_id=story_id, service="openrouter", model=model,
                                      purpose=purpose, status="success", duration_ms=duration_ms,
                                      request_text=user[:10000], response_text=content[:10000],
                                      tokens_in=usage.get("prompt_tokens"),
                                      tokens_out=usage.get("completion_tokens")))
                    return content
        except Exception as e:
            duration_ms = int((time.time() - t0) * 1000)
            logger.warning("LLM error (attempt %d): %s", attempt, e)
            fire(log_api_call(story_id=story_id, service="openrouter", model=model,
                              purpose=purpose, status="failed", duration_ms=duration_ms,
                              request_text=user[:10000], error=str(e)[:1000]))

    raise RuntimeError("LLM failed after all retries")


def _extract_json(text: str) -> dict:
    """Extract JSON from LLM response, stripping markdown fences."""
    cleaned = re.sub(r"```(?:json)?\s*", "", text)
    cleaned = cleaned.strip().rstrip("`")

    # Try to find JSON object
    start = cleaned.find("{")
    if start == -1:
        raise ValueError("No JSON object found in response")

    depth = 0
    end = start
    for i in range(start, len(cleaned)):
        if cleaned[i] == "{":
            depth += 1
        elif cleaned[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    return json.loads(cleaned[start:end])


async def generate_screenplay(context: str, story_id: int = None) -> dict:
    """Generate a structured fairy tale screenplay.

    Args:
        context: Topic and child info from user.

    Returns:
        Dict with keys: title, characters, segments, scenes.
    """
    from db.config_manager import cfg
    screenwriter_prompt = await cfg.get("prompt.screenwriter", SCREENWRITER_PROMPT)
    system_prompt = await cfg.get("prompt.screenwriter_system",
                                   "Ты генерируешь ТОЛЬКО валидный JSON. Никакого текста до или после JSON.")
    prompt = screenwriter_prompt.format(context=context)

    for attempt in range(1, 4):
        response = await _call_llm(
            system=system_prompt,
            user=prompt,
            story_id=story_id,
            purpose="screenplay",
        )
        logger.info("Screenplay LLM response (attempt %d): length=%d, start=%s",
                     attempt, len(response) if response else 0,
                     (response[:100] if response else "EMPTY"))

        if not response or not response.strip():
            logger.warning("Empty screenplay response (attempt %d)", attempt)
            continue

        try:
            screenplay = _extract_json(response)
            break
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("Screenplay JSON parse failed (attempt %d): %s", attempt, e)
            if attempt == 3:
                raise

    # Validate required fields
    required = {"title", "characters", "segments"}
    missing = required - set(screenplay.keys())
    if missing:
        raise ValueError(f"Screenplay missing fields: {missing}")

    # Ensure narrator exists
    char_ids = {c["id"] for c in screenplay["characters"]}
    if "narrator" not in char_ids:
        raise ValueError("Screenplay must have a 'narrator' character")

    # Validate segments
    for i, seg in enumerate(screenplay["segments"]):
        if seg["character_id"] not in char_ids:
            raise ValueError(f"Segment {i} references unknown character: {seg['character_id']}")
        if len(seg["text"]) > 250:
            # Truncate overly long segments
            seg["text"] = seg["text"][:247] + "..."

    logger.info(
        "Screenplay generated: '%s', %d characters, %d segments",
        screenplay["title"],
        len(screenplay["characters"]),
        len(screenplay["segments"]),
    )
    return screenplay
