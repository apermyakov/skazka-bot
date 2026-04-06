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
Каждая строка текста пронумерована [0], [1], [2]... — это номера сегментов.
Раздели сценарий на 7-8 ключевых сцен для иллюстраций.

Сценарий:
Название: {title}
Персонажи: {characters}
Текст:
{story_text}

Верни ТОЛЬКО JSON без markdown:
{{
  "character_appearances": {{
    "имя_персонажа": "внешность: цвет волос/шерсти, глаз, одежда"
  }},
  "scenes": [
    {{
      "scene_index": 0,
      "segment_start": 0,
      "segment_end": 3,
      "description": "Что происходит визуально (макс 10 слов)",
      "characters_present": ["имя1"],
      "setting": "лес",
      "mood": "радостный"
    }}
  ]
}}

ПРАВИЛА:
1. Ровно 7-8 сцен
2. segment_start и segment_end — диапазон номеров сегментов для этой сцены (segment_end НЕ включается)
3. Сцены должны покрывать ВСЕ сегменты без пропусков и пересечений
4. Первая сцена — начало, последняя — счастливый финал
5. Описание сцены — МАКСИМУМ 10 слов
6. Главный герой-ребёнок присутствует в каждой сцене
7. character_appearances ОБЯЗАТЕЛЕН — опиши внешность КАЖДОГО персонажа (кроме рассказчика)
8. Если в тексте указан цвет (серый кот, рыжая лиса) — ОБЯЗАТЕЛЬНО укажи этот цвет
"""


async def split_into_scenes(screenplay: dict) -> list[dict]:
    """Split screenplay into 7-8 key visual scenes for illustration."""
    title = screenplay["title"]
    characters = ", ".join(c["name"] for c in screenplay["characters"] if c["id"] != "narrator")

    # Build numbered story text (segment indices for scene mapping)
    story_lines = []
    for idx, seg in enumerate(screenplay["segments"]):
        raw = seg["text"]
        clean = re.sub(r'\[[\w\s]+\]', '', raw).strip()
        clean = re.sub(r'\s{2,}', ' ', clean)
        if clean:
            story_lines.append(f"[{idx}] {clean}")
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
        "max_tokens": 8000,
    }

    for attempt in range(1, 6):
        if attempt > 1:
            await asyncio.sleep(3)  # wait between retries

        async with aiohttp.ClientSession() as session:
            async with session.post(OPENROUTER_URL, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=120)) as resp:
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

        # Parse JSON — strip markdown fences and find the JSON object
        cleaned = re.sub(r"```(?:json)?\s*", "", text)
        cleaned = re.sub(r"```\s*$", "", cleaned).strip()

        try:
            result = json.loads(cleaned)
        except json.JSONDecodeError:
            # Find JSON object by matching braces
            start = cleaned.find("{")
            if start == -1:
                logger.warning("No JSON object in scene split response (attempt %d): %s", attempt, cleaned[:200])
                continue

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

            try:
                result = json.loads(cleaned[start:end])
            except json.JSONDecodeError:
                # Try to repair truncated JSON by closing open brackets
                fragment = cleaned[start:]
                open_braces = fragment.count("{") - fragment.count("}")
                open_brackets = fragment.count("[") - fragment.count("]")
                repaired = fragment + "]" * open_brackets + "}" * open_braces
                try:
                    result = json.loads(repaired)
                    logger.info("Repaired truncated JSON (attempt %d)", attempt)
                except json.JSONDecodeError as e:
                    logger.warning("Invalid JSON in scene split (attempt %d): %s", attempt, e)
                    continue

        scenes = result.get("scenes", [])
        character_appearances = result.get("character_appearances", {})

        if not scenes:
            logger.warning("No scenes in parsed result (attempt %d)", attempt)
            continue

        break
    else:
        raise RuntimeError("Scene split failed after 5 attempts")

    logger.info("Split into %d scenes for illustration, appearances: %s", len(scenes), character_appearances)
    return scenes, character_appearances


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
                logger.info("Image API response for scene %d: keys=%s, content_type=%s, refusal=%s, content=%s",
                            scene_index, list(message.keys()),
                            type(message.get("content")).__name__,
                            str(message.get("refusal", ""))[:300],
                            str(message.get("content", ""))[:300])

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
    character_appearances: dict[str, str],
    previous_scene_desc: str | None,
    style_block: str,
    style_suffix: str,
) -> str:
    """Build the text prompt for a single illustration."""
    continuity = ""
    if previous_scene_desc:
        continuity = f"\nPrevious scene showed: {previous_scene_desc}. This scene continues the same story."

    # Build appearance block for characters in this scene
    appearance_lines = []
    for char_name in scene.get("characters_present", []):
        desc = character_appearances.get(char_name, "")
        if desc:
            appearance_lines.append(f"  - {char_name}: {desc}")
    appearance_block = ""
    if appearance_lines:
        appearance_block = (
            "\n\nCHARACTER APPEARANCES (MUST match exactly in every scene):\n"
            + "\n".join(appearance_lines)
        )

    return (
        f"{style_block}\n\n"
        f"Fairy tale: '{fairy_tale_title}'\n"
        f"Characters: {characters_desc}\n"
        f"Scene {scene_index + 1} of {total_scenes}: {scene.get('title', '')}\n"
        f"Setting: {scene.get('setting', 'forest')}\n"
        f"Mood: {scene.get('mood', 'magical')}\n"
        f"Visual description: {scene.get('description', '')}\n"
        f"{appearance_block}"
        f"{continuity}\n\n"
        f"Generate a single children's book illustration for this scene. "
        f"IMPORTANT: Each character appears EXACTLY ONCE. Do NOT duplicate any character or animal. "
        f"Characters in this scene: {', '.join(scene.get('characters_present', []))} — draw each one ONLY ONCE. "
        f"CRITICAL: Each character's appearance (fur color, hair color, clothing) must be IDENTICAL across all scenes. "
        f"{style_suffix}"
    )


async def _face_swap_replicate(
    illustration_bytes: bytes,
    face_photo_b64: str,
) -> bytes | None:
    """Swap the face from the photo onto the illustration via Replicate."""
    token = settings.replicate_api_token
    if not token:
        return illustration_bytes  # no token → return as-is

    illustration_b64 = base64.b64encode(illustration_bytes).decode()

    face_url = face_photo_b64
    if not face_url.startswith("data:"):
        face_url = f"data:image/jpeg;base64,{face_photo_b64}"

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    try:
        async with aiohttp.ClientSession() as session:
            # Get model version
            async with session.get(
                "https://api.replicate.com/v1/models/codeplugtech/face-swap",
                headers=headers, timeout=aiohttp.ClientTimeout(total=15),
            ) as r:
                model = await r.json()
                version = model["latest_version"]["id"]

            # Create prediction
            payload = {
                "version": version,
                "input": {
                    "input_image": f"data:image/png;base64,{illustration_b64}",
                    "swap_image": face_url,
                },
            }
            async with session.post(
                "https://api.replicate.com/v1/predictions",
                headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=15),
            ) as r:
                pred = await r.json()
                pred_id = pred.get("id")
                if not pred_id:
                    logger.warning("Replicate face swap failed to start: %s", pred)
                    return illustration_bytes

            # Poll for result (max ~60s)
            for _ in range(30):
                await asyncio.sleep(2)
                async with session.get(
                    f"https://api.replicate.com/v1/predictions/{pred_id}",
                    headers=headers, timeout=aiohttp.ClientTimeout(total=10),
                ) as r:
                    d = await r.json()
                    st = d.get("status")
                    if st == "succeeded":
                        output = d.get("output")
                        img_url = output if isinstance(output, str) else (output[0] if isinstance(output, list) else None)
                        if img_url:
                            async with session.get(img_url, timeout=aiohttp.ClientTimeout(total=30)) as img_r:
                                if img_r.status == 200:
                                    result_bytes = await img_r.read()
                                    logger.info("Face swap complete: %d bytes", len(result_bytes))
                                    return result_bytes
                        return illustration_bytes
                    elif st == "failed":
                        logger.warning("Replicate face swap failed: %s", d.get("error"))
                        return illustration_bytes

            logger.warning("Replicate face swap timed out")
            return illustration_bytes

    except Exception as e:
        logger.warning("Face swap error: %s", e)
        return illustration_bytes


async def generate_illustration(
    scene: dict,
    scene_index: int,
    total_scenes: int,
    reference_photo_b64: str | None,
    previous_scene_desc: str | None,
    fairy_tale_title: str,
    characters_desc: str,
    character_appearances: dict[str, str] | None = None,
    reference_photos: list[str] | None = None,
) -> bytes | None:
    """Generate one Pixar-style illustration via Gemini, then face swap if photo provided."""

    # Build photo content from all reference photos (more photos = better face matching)
    photos = reference_photos or ([reference_photo_b64] if reference_photo_b64 else [])
    photo_content = []
    for photo in photos:
        if photo:
            photo_url = photo
            if not photo_url.startswith("data:"):
                photo_url = f"data:image/jpeg;base64,{photo}"
            photo_content.append({
                "type": "image_url",
                "image_url": {"url": photo_url},
            })

    face_suffix = ""
    if photo_content:
        if len(photo_content) > 1:
            face_suffix = (
                f"Reference: {len(photo_content)} photos of the same child from different angles. "
                "The main child character MUST closely match this child: "
                "same face shape, hair color, hair style, eye color, skin tone. "
                "Study ALL reference photos carefully to capture the child's true appearance."
            )
        else:
            face_suffix = (
                "The main child character MUST closely match the child in the reference photo: "
                "same face shape, hair color, hair style, eye color, skin tone."
            )

    prompt = _build_scene_prompt(
        scene, scene_index, total_scenes, fairy_tale_title, characters_desc,
        character_appearances or {},
        previous_scene_desc, STYLE_PIXAR,
        f"Pixar-style 3D render. {face_suffix}",
    )
    content = [{"type": "text", "text": prompt}] + photo_content

    img_bytes = await _call_image_api(content, scene_index, "pixar")

    # Step 2: Face swap if we have a photo and Replicate token
    if img_bytes and reference_photo_b64 and settings.replicate_api_token:
        logger.info("Running face swap for scene %d", scene_index)
        img_bytes = await _face_swap_replicate(img_bytes, reference_photo_b64)

    return img_bytes


async def generate_illustrations_batch(
    screenplay: dict,
    reference_photo_b64: str | None = None,
    reference_photos: list[str] | None = None,
    on_progress=None,
    on_illustration_ready: Callable[[int, bytes], Awaitable[None]] | None = None,
) -> list[bytes]:
    """Generate all Pixar-style illustrations for a fairy tale.

    Args:
        on_illustration_ready: Callback fired for each illustration as it's generated.
            Receives (scene_index, image_bytes).

    Returns list of PNG bytes (may contain None for failed scenes).
    """
    # Step 1: Split into scenes
    scenes, character_appearances = await split_into_scenes(screenplay)

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
            result = on_progress(f"🎨 Рисую иллюстрацию {i + 1}/{len(scenes)}...")
            if asyncio.iscoroutine(result):
                await result

        img_bytes = await generate_illustration(
            scene=scene,
            scene_index=i,
            total_scenes=len(scenes),
            reference_photo_b64=reference_photo_b64,
            previous_scene_desc=prev_desc,
            fairy_tale_title=title,
            characters_desc=characters_desc,
            character_appearances=character_appearances,
            reference_photos=reference_photos,
        )

        results.append(img_bytes)
        prev_desc = scene.get("description", "")

        logger.info(
            "Illustration %d/%d: %s",
            i + 1, len(scenes),
            f"{len(img_bytes):,}b" if img_bytes else "FAILED",
        )

        if img_bytes and on_illustration_ready:
            try:
                await on_illustration_ready(i, img_bytes)
            except Exception as e:
                logger.warning("on_illustration_ready callback failed for scene %d: %s", i, e)

    successful = sum(1 for r in results if r is not None)
    logger.info("Illustrations complete: %d/%d successful", successful, len(results))

    return results, scenes
