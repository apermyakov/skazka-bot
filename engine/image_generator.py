# -*- coding: utf-8 -*-
"""Illustration generation for fairy tales using Nano Banana Pro (Gemini 3 Pro Image)."""

import base64
import json
import logging
import re
from pathlib import Path

import aiohttp

from bot.config import settings

logger = logging.getLogger(__name__)

IMAGE_MODEL = "google/gemini-3-pro-image-preview"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

STYLE_BLOCK = (
    "Style: Children's book illustration, soft watercolor, warm gentle tones. "
    "Dreamy, cozy atmosphere like classic fairy tale books. "
    "No text, no letters, no words on the image. "
    "Consistent color palette throughout the series. "
    "Characters should look exactly the same across all illustrations."
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
      "description": "Подробное визуальное описание: что происходит, кто где стоит, какие эмоции, окружение, освещение. 2-3 предложения.",
      "characters_present": ["имя1", "имя2"],
      "setting": "лес / пещера / поляна / дом / небо",
      "mood": "радостный / таинственный / грустный / волшебный"
    }}
  ]
}}

ПРАВИЛА:
1. Ровно 4-5 сцен
2. Первая сцена — начало, последняя — счастливый финал
3. Описание должно быть ВИЗУАЛЬНЫМ (что видит художник), не пересказом текста
4. Главный герой-ребёнок присутствует в каждой сцене
5. Описания конкретные: позы, выражения лиц, предметы, цвета
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
        "max_tokens": 2000,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(OPENROUTER_URL, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=60)) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"Scene split LLM error {resp.status}: {body[:200]}")
            data = await resp.json()
            text = data["choices"][0]["message"]["content"]

    # Parse JSON
    cleaned = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`")
    start = cleaned.find("{")
    if start == -1:
        raise ValueError("No JSON in scene split response")

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

    result = json.loads(cleaned[start:end])
    scenes = result.get("scenes", [])

    if not scenes:
        raise ValueError("No scenes generated")

    logger.info("Split into %d scenes for illustration", len(scenes))
    return scenes


async def generate_illustration(
    scene: dict,
    scene_index: int,
    total_scenes: int,
    reference_photo_b64: str | None,
    previous_scene_desc: str | None,
    fairy_tale_title: str,
    characters_desc: str,
) -> bytes | None:
    """Generate one illustration using Nano Banana Pro."""

    # Build the prompt
    continuity = ""
    if previous_scene_desc:
        continuity = f"\nPrevious scene showed: {previous_scene_desc}. This scene continues the same story."

    prompt = (
        f"{STYLE_BLOCK}\n\n"
        f"Fairy tale: '{fairy_tale_title}'\n"
        f"Characters: {characters_desc}\n"
        f"Scene {scene_index + 1} of {total_scenes}: {scene.get('title', '')}\n"
        f"Setting: {scene.get('setting', 'forest')}\n"
        f"Mood: {scene.get('mood', 'magical')}\n"
        f"Visual description: {scene.get('description', '')}\n"
        f"{continuity}\n\n"
        f"Generate a single children's book illustration for this scene. "
        f"The main character is the child from the reference photo (if provided). "
        f"Make the child recognizable but in illustrated style."
    )

    content = [{"type": "text", "text": prompt}]

    if reference_photo_b64:
        # Add reference photo
        if not reference_photo_b64.startswith("data:"):
            reference_photo_b64 = f"data:image/jpeg;base64,{reference_photo_b64}"
        content.append({
            "type": "image_url",
            "image_url": {"url": reference_photo_b64},
        })

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
        async with aiohttp.ClientSession() as session:
            async with session.post(
                OPENROUTER_URL, json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning("Image gen failed for scene %d: HTTP %d: %s", scene_index, resp.status, body[:200])
                    return None

                data = await resp.json()
                message = data["choices"][0]["message"]

                # Extract image from response
                images = message.get("images", [])
                if not images:
                    # Try alternate format
                    content_parts = message.get("content", "")
                    if isinstance(content_parts, list):
                        for part in content_parts:
                            if isinstance(part, dict) and part.get("type") == "image_url":
                                images.append(part)

                if not images:
                    logger.warning("No images in response for scene %d", scene_index)
                    return None

                img_url = images[0]
                if isinstance(img_url, dict):
                    img_url = img_url.get("image_url", {}).get("url", "")

                if img_url.startswith("data:"):
                    # Extract base64 from data URL
                    b64_data = img_url.split(",", 1)[1] if "," in img_url else img_url
                    return base64.b64decode(b64_data)
                else:
                    logger.warning("Unexpected image format for scene %d", scene_index)
                    return None

    except Exception as e:
        logger.error("Image generation error for scene %d: %s", scene_index, e)
        return None


async def generate_illustrations_batch(
    screenplay: dict,
    reference_photo_b64: str | None = None,
    on_progress: callable = None,
) -> list[bytes]:
    """Generate all illustrations for a fairy tale.

    Returns list of PNG bytes (may contain None for failed scenes).
    """
    # Step 1: Split into scenes
    scenes = await split_into_scenes(screenplay)

    title = screenplay["title"]
    characters_desc = ", ".join(
        f"{c['name']} ({c.get('personality', '')})"
        for c in screenplay["characters"]
        if c["id"] != "narrator"
    )

    # Step 2: Generate illustrations sequentially (for style consistency)
    results = []
    prev_desc = None

    for i, scene in enumerate(scenes):
        if on_progress:
            await on_progress(f"🎨 Рисую иллюстрацию {i + 1}/{len(scenes)}...")

        img_bytes = await generate_illustration(
            scene=scene,
            scene_index=i,
            total_scenes=len(scenes),
            reference_photo_b64=reference_photo_b64,
            previous_scene_desc=prev_desc,
            fairy_tale_title=title,
            characters_desc=characters_desc,
        )

        results.append(img_bytes)
        prev_desc = scene.get("description", "")

        logger.info(
            "Illustration %d/%d: %s",
            i + 1, len(scenes),
            f"{len(img_bytes):,} bytes" if img_bytes else "FAILED",
        )

    successful = sum(1 for r in results if r is not None)
    logger.info("Illustrations complete: %d/%d successful", successful, len(results))

    return results
