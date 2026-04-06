# -*- coding: utf-8 -*-
"""Illustration generation for fairy tales using Nano Banana Pro (Gemini 3 Pro Image)."""

import asyncio
import base64
import json
import logging
import re
from pathlib import Path
from typing import Callable, Awaitable

import aiohttp

from bot.config import settings

logger = logging.getLogger(__name__)

IMAGE_MODEL = "google/gemini-2.5-flash-image"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

STYLE_PIXAR = (
    "Generate a wide landscape (16:9) Pixar-style 3D cartoon illustration. "
    "The character must be RECOGNIZABLE from the reference photo. "
    "STRICTLY NO text, words, letters, signs, or writing anywhere. "
    "Anatomically correct: exactly two arms, two hands per person. "
    "Each animal has exactly ONE head, ONE body, and the correct number of legs for its species. "
    "NEVER duplicate or merge animals — if the scene has one cat, draw exactly ONE cat. "
    "Warm, magical lighting. Rich, vibrant colors. "
    "Consistent style and color palette throughout the series."
)

STYLE_KIDS_DRAWING = (
    "Generate a wide landscape (16:9) illustration in the style of a high-quality children's book watercolor drawing. "
    "Hand-drawn feel with soft watercolor textures, gentle pencil outlines, and pastel colors. "
    "Like a beautiful illustration from a premium children's picture book — warm, cozy, slightly whimsical. "
    "NOT crude or messy — this is professional children's book art with a hand-crafted feel. "
    "STRICTLY NO text, words, letters, signs, or writing anywhere. "
    "Anatomically correct: exactly two arms, two hands per person. "
    "Each animal has exactly ONE head, ONE body, and the correct number of legs for its species. "
    "NEVER duplicate or merge animals — if the scene has one cat, draw exactly ONE cat. "
    "Soft, dreamy lighting. Gentle watercolor palette."
)

SCENE_SPLIT_PROMPT = """\
Ты — художественный редактор детской книги. Дан сценарий аудиосказки.
Раздели его на 4-5 ключевых сцен для иллюстраций.

Сценарий:
Название: {title}
Персонажи: {characters}
Текст:
{story_text}

Верни ТОЛЬКО JSON без markdown:
{{
  "scenes": [
    {{
      "scene_index": 0,
      "title": "Короткое название сцены",
      "description": "Краткое визуальное описание сцены. Максимум 1-2 предложения.",
      "characters_present": ["имя1", "имя2"],
      "setting": "лес / пещера / поляна / дом / небо",
      "mood": "радостный / таинственный / грустный / волшебный"
    }}
  ]
}}

ПРАВИЛА:
1. Ровно 4 сцены (не больше!)
2. Первая сцена — начало, последняя — счастливый финал
3. Описание — КОРОТКО, 1-2 предложения на сцену
4. Главный герой-ребёнок присутствует в каждой сцене
5. Весь JSON должен уместиться в 500 слов
"""


async def split_into_scenes(screenplay: dict) -> list[dict]:
    """Split screenplay into 4-5 key visual scenes for illustration."""
    title = screenplay["title"]
    characters = ", ".join(c["name"] for c in screenplay["characters"] if c["id"] != "narrator")

    # Build clean story text
    story_lines = []
    for seg in screenplay["segments"]:
        raw = seg["text"]
        clean = re.sub(r'\[[\w\s]+\]', '', raw).strip()
        clean = re.sub(r'\s{2,}', ' ', clean)
        if clean:
            story_lines.append(clean)
    story_text = "\n".join(story_lines)

    prompt = SCENE_SPLIT_PROMPT.format(
        title=title,
        characters=characters,
        story_text=story_text[:3000],
    )

    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": "Ты генерируешь ТОЛЬКО валидный JSON."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.5,
        "max_tokens": 4000,
    }

    import time as _time
    for attempt in range(1, 4):
        if attempt > 1:
            await asyncio.sleep(3)  # wait between retries

        async with aiohttp.ClientSession() as session:
            async with session.post(OPENROUTER_URL, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=90)) as resp:
                raw = await resp.text()
                logger.info("Scene split HTTP %d (attempt %d), body length: %d", resp.status, attempt, len(raw))

                if resp.status != 200:
                    logger.warning("Scene split error (attempt %d): %s", attempt, raw[:300])
                    continue

                if not raw or not raw.strip():
                    logger.warning("Empty scene split body (attempt %d)", attempt)
                    continue

                data = json.loads(raw)

        text = data["choices"][0]["message"]["content"]
        logger.info("Scene split content (attempt %d): %s", attempt, text[:200] if text else "EMPTY")

        if not text or not text.strip():
            logger.warning("Empty scene split content (attempt %d)", attempt)
            continue

        break
    else:
        raise RuntimeError("Scene split failed after 3 attempts")

    # Parse JSON — strip markdown fences and find the JSON object
    cleaned = re.sub(r"```(?:json)?\s*", "", text)
    cleaned = re.sub(r"```\s*$", "", cleaned).strip()

    # Try direct parse first
    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError:
        # Find JSON object by matching braces
        start = cleaned.find("{")
        if start == -1:
            raise ValueError(f"No JSON object in scene split response: {cleaned[:200]}")

        depth = 0
        end = len(cleaned)
        for i in range(start, len(cleaned)):
            if cleaned[i] == "{":
                depth += 1
            elif cleaned[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break

        result = json.loads(cleaned[start:end])
    scenes = result.get("scenes", [])

    if not scenes:
        raise ValueError("No scenes generated")

    logger.info("Split into %d scenes for illustration", len(scenes))
    return scenes


async def _call_image_api(content: list[dict], scene_index: int, style_label: str) -> bytes | None:
    """Send image generation request to OpenRouter and return image bytes."""
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": IMAGE_MODEL,
        "modalities": ["image", "text"],
        "messages": [{"role": "user", "content": content}],
        "image_config": {
            "aspect_ratio": "16:9",
            "image_size": "2K",
        },
    }

    try:
        logger.info("Generating illustration %d [%s]", scene_index, style_label)
        async with aiohttp.ClientSession() as session:
            async with session.post(
                OPENROUTER_URL, json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning("Image gen failed for scene %d [%s]: HTTP %d: %s",
                                   scene_index, style_label, resp.status, body[:300])
                    return None

                raw_body = await resp.text()
                if not raw_body or not raw_body.strip():
                    logger.warning("Empty response body for scene %d [%s]", scene_index, style_label)
                    return None

                try:
                    data = json.loads(raw_body)
                except Exception as je:
                    logger.warning("JSON parse error for scene %d [%s]: %s | body: %s",
                                   scene_index, style_label, je, raw_body[:300])
                    return None

                message = data["choices"][0]["message"]

                images = message.get("images", [])
                if not images:
                    content_parts = message.get("content", "")
                    if isinstance(content_parts, list):
                        for part in content_parts:
                            if isinstance(part, dict) and part.get("type") == "image_url":
                                images.append(part)

                if not images:
                    logger.warning("No images in response for scene %d [%s]", scene_index, style_label)
                    return None

                img_url = images[0]
                if isinstance(img_url, dict):
                    img_url = img_url.get("image_url", {}).get("url", "")

                if img_url.startswith("data:"):
                    b64_data = img_url.split(",", 1)[1] if "," in img_url else img_url
                    return base64.b64decode(b64_data)
                else:
                    logger.warning("Unexpected image format for scene %d [%s]", scene_index, style_label)
                    return None

    except Exception as e:
        import traceback
        logger.error("Image generation error for scene %d [%s]: %s\n%s",
                     scene_index, style_label, e, traceback.format_exc())
        return None


def _build_scene_prompt(
    scene: dict,
    scene_index: int,
    total_scenes: int,
    fairy_tale_title: str,
    characters_desc: str,
    previous_scene_desc: str | None,
    style_block: str,
    style_suffix: str,
) -> str:
    """Build the text prompt for a single illustration."""
    continuity = ""
    if previous_scene_desc:
        continuity = f"\nPrevious scene showed: {previous_scene_desc}. This scene continues the same story."

    return (
        f"{style_block}\n\n"
        f"Fairy tale: '{fairy_tale_title}'\n"
        f"Characters: {characters_desc}\n"
        f"Scene {scene_index + 1} of {total_scenes}: {scene.get('title', '')}\n"
        f"Setting: {scene.get('setting', 'forest')}\n"
        f"Mood: {scene.get('mood', 'magical')}\n"
        f"Visual description: {scene.get('description', '')}\n"
        f"{continuity}\n\n"
        f"Generate a single children's book illustration for this scene. "
        f"IMPORTANT: Each character appears EXACTLY ONCE. Do NOT duplicate any character or animal. "
        f"Characters in this scene: {', '.join(scene.get('characters_present', []))} — draw each one ONLY ONCE. "
        f"{style_suffix}"
    )


async def generate_illustration(
    scene: dict,
    scene_index: int,
    total_scenes: int,
    reference_photo_b64: str | None,
    previous_scene_desc: str | None,
    fairy_tale_title: str,
    characters_desc: str,
) -> tuple[bytes | None, bytes | None]:
    """Generate two illustration variants (Pixar + kids drawing) in parallel.

    Returns (pixar_bytes, kids_drawing_bytes).
    """
    photo_content = []
    if reference_photo_b64:
        photo_url = reference_photo_b64
        if not photo_url.startswith("data:"):
            photo_url = f"data:image/jpeg;base64,{photo_url}"
        photo_content = [{
            "type": "image_url",
            "image_url": {"url": photo_url},
        }]

    face_suffix = (
        "The child from the reference photo MUST be RECOGNIZABLE. "
        "Same face shape, hair color, hair style, eye color, skin tone. "
        "The child and their parents must immediately recognize them."
    ) if reference_photo_b64 else ""

    # Pixar prompt
    pixar_prompt = _build_scene_prompt(
        scene, scene_index, total_scenes, fairy_tale_title, characters_desc,
        previous_scene_desc, STYLE_PIXAR,
        f"Pixar-style 3D render. {face_suffix}",
    )
    pixar_content = [{"type": "text", "text": pixar_prompt}] + photo_content

    # Kids drawing prompt
    kids_prompt = _build_scene_prompt(
        scene, scene_index, total_scenes, fairy_tale_title, characters_desc,
        previous_scene_desc, STYLE_KIDS_DRAWING,
        f"Professional children's book watercolor illustration. {face_suffix}",
    )
    kids_content = [{"type": "text", "text": kids_prompt}] + photo_content

    # Generate both in parallel
    pixar_task = asyncio.create_task(_call_image_api(pixar_content, scene_index, "pixar"))
    kids_task = asyncio.create_task(_call_image_api(kids_content, scene_index, "watercolor"))

    pixar_bytes = await pixar_task
    kids_bytes = await kids_task

    return pixar_bytes, kids_bytes


async def generate_illustrations_batch(
    screenplay: dict,
    reference_photo_b64: str | None = None,
    on_progress=None,
    on_illustration_ready: Callable[[int, bytes, str], Awaitable[None]] | None = None,
) -> list[bytes]:
    """Generate all illustrations for a fairy tale (two styles per scene).

    Args:
        on_illustration_ready: Callback fired for each illustration as it's generated.
            Receives (scene_index, image_bytes, style_label).

    Returns list of Pixar PNG bytes (may contain None for failed scenes).
    """
    # Step 1: Split into scenes
    scenes = await split_into_scenes(screenplay)

    title = screenplay["title"]
    characters_desc = ", ".join(
        f"{c['name']} ({c.get('personality', '')})"
        for c in screenplay["characters"]
        if c["id"] != "narrator"
    )

    # Step 2: Generate illustrations sequentially per scene (two styles in parallel)
    results = []
    prev_desc = None

    for i, scene in enumerate(scenes):
        if on_progress:
            result = on_progress(f"🎨 Рисую иллюстрацию {i + 1}/{len(scenes)}...")
            if asyncio.iscoroutine(result):
                await result

        pixar_bytes, kids_bytes = await generate_illustration(
            scene=scene,
            scene_index=i,
            total_scenes=len(scenes),
            reference_photo_b64=reference_photo_b64,
            previous_scene_desc=prev_desc,
            fairy_tale_title=title,
            characters_desc=characters_desc,
        )

        # Use pixar as the primary result (for video)
        results.append(pixar_bytes)
        prev_desc = scene.get("description", "")

        logger.info(
            "Illustration %d/%d: pixar=%s, watercolor=%s",
            i + 1, len(scenes),
            f"{len(pixar_bytes):,}b" if pixar_bytes else "FAILED",
            f"{len(kids_bytes):,}b" if kids_bytes else "FAILED",
        )

        # Deliver both styles immediately
        if on_illustration_ready:
            if pixar_bytes:
                try:
                    await on_illustration_ready(i, pixar_bytes, "pixar")
                except Exception as e:
                    logger.warning("on_illustration_ready callback failed for scene %d pixar: %s", i, e)
            if kids_bytes:
                try:
                    await on_illustration_ready(i, kids_bytes, "watercolor")
                except Exception as e:
                    logger.warning("on_illustration_ready callback failed for scene %d watercolor: %s", i, e)

    successful = sum(1 for r in results if r is not None)
    logger.info("Illustrations complete: %d/%d successful", successful, len(results))

    return results
