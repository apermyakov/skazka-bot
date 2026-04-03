# -*- coding: utf-8 -*-
"""Async LLM client via OpenRouter (Gemini 2.5 Pro)."""

import json
import logging
import re

import aiohttp

from bot.config import settings
from engine.story_parser import SCREENWRITER_PROMPT

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


async def _call_llm(system: str, user: str, max_retries: int = 3) -> str:
    """Call OpenRouter API and return the assistant's text response."""
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.8,
        "max_tokens": 8000,
    }

    for attempt in range(1, max_retries + 1):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(OPENROUTER_URL, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.warning("LLM HTTP %d (attempt %d): %s", resp.status, attempt, body[:300])
                        continue
                    data = await resp.json()
                    return data["choices"][0]["message"]["content"]
        except Exception as e:
            logger.warning("LLM error (attempt %d): %s", attempt, e)

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


async def generate_screenplay(context: str) -> dict:
    """Generate a structured fairy tale screenplay.

    Args:
        context: Topic and child info from user.

    Returns:
        Dict with keys: title, characters, segments, scenes.
    """
    prompt = SCREENWRITER_PROMPT.format(context=context)
    response = await _call_llm(
        system="Ты генерируешь ТОЛЬКО валидный JSON. Никакого текста до или после JSON.",
        user=prompt,
    )
    screenplay = _extract_json(response)

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
