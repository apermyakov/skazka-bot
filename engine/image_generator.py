# -*- coding: utf-8 -*-
"""Illustration generation for fairy tales using Nano Banana Pro (Gemini 3 Pro Image)."""

import asyncio
import base64
import json
import logging
import re
import time
from pathlib import Path
from typing import Callable, Awaitable

import aiohttp

from bot.config import settings
from db.database import log_api_call, fire

logger = logging.getLogger(__name__)

IMAGE_MODEL = "google/gemini-2.5-flash-image"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
IMAGE_MAX_ATTEMPTS = 3
IMAGE_RETRY_DELAY = 2.0

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

STYLE_PAINTED = (
    "Generate a wide landscape (16:9) classic hand-PAINTED fairy-tale storybook illustration — "
    "rich gouache and oil painting with visible brushwork, warm golden light, painterly textures, "
    "the timeless look of a treasured children's picture book. Painterly, not flat vector, not photographic. "
    "The main child character must be RECOGNIZABLE from the reference photo. "
    "STRICTLY NO text, words, letters, signs, or writing anywhere. "
    "Anatomically correct: exactly two arms, two hands per person. "
    "Each animal has exactly ONE head, ONE body, and the correct number of legs for its species. "
    "NEVER duplicate or merge animals — if the scene has one cat, draw exactly ONE cat. "
    "Warm, magical lighting. Consistent painterly style and palette throughout the series."
)

STYLE_REALISTIC = (
    "Generate a wide landscape (16:9) polished SEMI-REALISTIC 3D render with realistic facial "
    "proportions, skin and detail (close to a real child's face while still gentle and animated), "
    "soft cinematic lighting, high detail. Prioritise faithful real facial likeness from the "
    "reference photo over cartoon stylisation. "
    "STRICTLY NO text, words, letters, signs, or writing anywhere. "
    "Anatomically correct: exactly two arms, two hands per person. "
    "Each animal has exactly ONE head, ONE body, and the correct number of legs for its species. "
    "NEVER duplicate or merge animals — if the scene has one cat, draw exactly ONE cat. "
    "Warm, magical lighting. Consistent style throughout the series."
)

# style key → (config key, code fallback). Used to resolve the style block text.
STYLE_CONFIG_KEYS = {
    "painted": ("prompt.style.painted", STYLE_PAINTED),
    "watercolor": ("prompt.style_kids_drawing", STYLE_KIDS_DRAWING),
    "realistic": ("prompt.style.realistic", STYLE_REALISTIC),
    "pixar": ("prompt.style_pixar", STYLE_PIXAR),
}
DEFAULT_STYLE = "painted"

# Image models love sprinkling comic sound-effects / captions (often garbled Cyrillic like
# "ДЗІН-ДЗІН"). Append this hard rule to every image prompt to keep illustrations 100% wordless.
NO_TEXT_RULE = (
    "CRITICAL: the image must be 100% WORDLESS. Absolutely NO text anywhere — no letters, words, "
    "numbers, captions, titles, signs, labels, speech bubbles, and especially NO sound-effect or "
    "onomatopoeia words (like 'ding', 'дзинь', 'дзін', 'бом', 'ring') in ANY language or alphabet. "
    "Do not write the sound a bell or object makes. Output ONLY the picture."
)


async def _resolve_style_block(style: str | None) -> str:
    """Resolve the style instruction text for a style key, from config with a constant fallback."""
    from db.config_manager import cfg
    key, fallback = STYLE_CONFIG_KEYS.get(style or DEFAULT_STYLE, STYLE_CONFIG_KEYS[DEFAULT_STYLE])
    return await cfg.get(key, fallback)

SCENE_SPLIT_PROMPT = """\
Ты — режиссёр раскадровки детской аудиосказки. Озвучка УЖЕ записана.
Каждая строка пронумерована [i] и помечена таймкодом [at Xs, dur Ys] — это КОГДА и сколько звучит сегмент.

Сценарий:
Название: {title}
Персонажи: {characters}
Таймлайн:
{story_text}

Составь РАСКАДРОВКУ — последовательность кадров (иллюстраций), синхронных с озвучкой.

ГЛАВНОЕ ПРАВИЛО: картинка кадра появляется на экране РОВНО на той строке, где ВПЕРВЫЕ
произносится то, что на ней изображено — НИКОГДА не раньше. Поэтому:
- segment_start кадра = номер сегмента, где это визуальное событие впервые озвучивается;
- description описывает то, что видно ИМЕННО В ЭТОТ момент (что нового только что ввёл рассказчик),
  а НЕ кульминацию всего блока и не то, что будет дальше.
Кадр держится на экране до следующего кадра.

Верни ТОЛЬКО JSON без markdown:
{{
  "character_appearances": {{
    "имя_персонажа": "внешность: цвет волос/шерсти, глаз, одежда"
  }},
  "scenes": [
    {{
      "scene_index": 0,
      "segment_start": 0,
      "description": "что видно В ЭТОТ момент (макс 12 слов)",
      "characters_present": ["имя1"],
      "setting": "лес",
      "mood": "спокойный"
    }}
  ]
}}

ПРАВИЛА:
1. Кадр 0 ОБЯЗАТЕЛЬНО имеет segment_start=0 (открывающая сцена).
2. Новый кадр — ТОЛЬКО когда визуальная ситуация заметно меняется: новое место, появляется
   важный персонаж/предмет, ключевой поворот. НЕ создавай похожие кадры в одной обстановке.
   ВАЖНО: когда в истории ВПЕРВЫЕ появляется персонаж или предмет (мишка, колокольчик и т.п.),
   привяжи кадр к сегменту, где о нём СКАЗАЛИ впервые, и НЕ показывай его в более ранних кадрах.
3. characters_present — только те, кто УЖЕ в кадре на момент segment_start. НЕ добавляй тех,
   кто появится позже в этом отрезке: персонаж не должен быть виден на картинке раньше, чем о нём сказали.
4. segment_start строго возрастает; кадры покрывают всю сказку до конца.
5. 4-6 кадров на сказку. Соседние кадры — не ближе ~25 секунд по таймкоду (смотри [at Xs]),
   иначе картинки мелькают и выглядят как дубли.
6. description — что видно В МОМЕНТ segment_start, МАКСИМУМ 12 слов. НЕ описывай будущее или
   развязку блока — только текущий момент, который сейчас звучит.
7. Главный герой-ребёнок присутствует в кадрах, где он есть по сюжету.
8. character_appearances ОБЯЗАТЕЛЕН — опиши внешность КАЖДОГО персонажа (кроме рассказчика).
9. Если в тексте указан цвет (серый кот, рыжая лиса) — ОБЯЗАТЕЛЬНО укажи этот цвет.
"""


async def split_into_scenes(screenplay: dict, story_id: int = None,
                            timeline_text: str | None = None) -> list[dict]:
    """Split screenplay into 7-8 key visual scenes for illustration."""
    title = screenplay["title"]
    characters = ", ".join(c["name"] for c in screenplay["characters"] if c["id"] != "narrator")

    # Use timeline with real timecodes if available, otherwise build from text
    if timeline_text:
        story_text = timeline_text
    else:
        char_names = {c["id"]: c["name"] for c in screenplay.get("characters", [])}
        story_lines = []
        for idx, seg in enumerate(screenplay["segments"]):
            raw = seg["text"]
            clean = re.sub(r'\[[\w\s]+\]', '', raw).strip()
            clean = re.sub(r'\s{2,}', ' ', clean)
            if clean:
                speaker = char_names.get(seg.get("character_id", ""), "?")
                story_lines.append(f"[{idx}] ({speaker}) {clean}")
        story_text = "\n".join(story_lines)

    from db.config_manager import cfg
    scene_split_prompt = await cfg.get("prompt.scene_split", SCENE_SPLIT_PROMPT)
    max_chars = await cfg.get("llm.story_text_max_chars", 3000)
    split_temp = await cfg.get("llm.scene_split_temperature", 0.5)

    prompt = scene_split_prompt.format(
        title=title,
        characters=characters,
        story_text=story_text[:max_chars],
    )

    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
    }
    # scene_split is a structured task (JSON markup) — use a non-thinking Flash
    # model. Pro burns the token budget on reasoning and returns empty content.
    llm_model = await cfg.get("model.scene_split", "google/gemini-3.5-flash")

    payload = {
        "model": llm_model,
        "messages": [
            {"role": "system", "content": "Ты генерируешь ТОЛЬКО валидный JSON."},
            {"role": "user", "content": prompt},
        ],
        "temperature": split_temp,
    }

    for attempt in range(1, 6):
        if attempt > 1:
            await asyncio.sleep(3)  # wait between retries

        t0 = time.time()
        from engine.http_session import get_session
        session = get_session()
        try:
            async with session.post(OPENROUTER_URL, json=payload, headers=headers) as resp:
                raw = await resp.text()
                duration_ms = int((time.time() - t0) * 1000)
                logger.info("Scene split HTTP %d (attempt %d), body length: %d", resp.status, attempt, len(raw))

                if resp.status != 200:
                    logger.warning("Scene split error (attempt %d): %s", attempt, raw[:300])
                    fire(log_api_call(story_id=story_id, service="openrouter", model=llm_model,
                                      purpose="scene_split", status="failed", duration_ms=duration_ms,
                                      error=raw[:1000]))
                    continue

                if not raw or not raw.strip():
                    logger.warning("Empty scene split body (attempt %d)", attempt)
                    continue

                data = json.loads(raw)
        except asyncio.TimeoutError:
            logger.warning("Scene split TIMEOUT after %dms (attempt %d) — retrying", int((time.time() - t0) * 1000), attempt)
            continue
        except aiohttp.ClientError as e:
            logger.warning("Scene split network error (attempt %d): %s — retrying", attempt, e)
            continue

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

        fire(log_api_call(story_id=story_id, service="openrouter", model=llm_model,
                          purpose="scene_split", status="success", duration_ms=duration_ms,
                          request_text=prompt[:10000], response_text=text[:10000]))
        break
    else:
        raise RuntimeError("Scene split failed after 5 attempts")

    logger.info("Split into %d scenes for illustration, appearances: %s", len(scenes), character_appearances)
    return scenes, character_appearances


async def _call_image_api(content: list[dict], scene_index: int, style_label: str,
                          story_id: int = None) -> bytes | None:
    """Send image generation request to OpenRouter and return image bytes."""
    from db.config_manager import cfg
    image_model = await cfg.get("model.image", IMAGE_MODEL)
    image_size = await cfg.get("image.size", "1K")

    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": image_model,
        "modalities": ["image", "text"],
        "messages": [{"role": "user", "content": content}],
        "image_config": {
            "aspect_ratio": "16:9",
            "image_size": image_size,
        },
    }

    # Extract text prompt for logging (skip base64 images)
    prompt_text = " ".join(p.get("text", "") for p in content if p.get("type") == "text")[:5000]

    from engine.http_session import get_session

    last_error = "unknown"
    for attempt in range(1, IMAGE_MAX_ATTEMPTS + 1):
        t0 = time.time()
        logger.info("Generating illustration %d [%s] (attempt %d/%d)",
                    scene_index, style_label, attempt, IMAGE_MAX_ATTEMPTS)
        try:
            session = get_session()
            async with session.post(
                    OPENROUTER_URL, json=payload, headers=headers,
                ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning("Image gen HTTP %d for scene %d [%s] (attempt %d): %s",
                                   resp.status, scene_index, style_label, attempt, body[:300])
                    last_error = f"HTTP {resp.status}"
                    if attempt < IMAGE_MAX_ATTEMPTS:
                        await asyncio.sleep(IMAGE_RETRY_DELAY)
                    continue

                raw_body = await resp.text()
                if not raw_body or not raw_body.strip():
                    logger.warning("Empty response body for scene %d [%s] (attempt %d)",
                                   scene_index, style_label, attempt)
                    last_error = "empty body"
                    if attempt < IMAGE_MAX_ATTEMPTS:
                        await asyncio.sleep(IMAGE_RETRY_DELAY)
                    continue

                try:
                    data = json.loads(raw_body)
                except Exception as je:
                    logger.warning("JSON parse error for scene %d [%s] (attempt %d): %s | body: %s",
                                   scene_index, style_label, attempt, je, raw_body[:300])
                    last_error = "json parse"
                    if attempt < IMAGE_MAX_ATTEMPTS:
                        await asyncio.sleep(IMAGE_RETRY_DELAY)
                    continue

                message = data["choices"][0]["message"]
                refusal = message.get("refusal")
                logger.info("Image API response for scene %d (attempt %d): keys=%s, content_type=%s, refusal=%s, content=%s",
                            scene_index, attempt, list(message.keys()),
                            type(message.get("content")).__name__,
                            str(refusal or "")[:300],
                            str(message.get("content", ""))[:300])

                images = message.get("images", [])
                if not images:
                    content_parts = message.get("content", "")
                    if isinstance(content_parts, list):
                        for part in content_parts:
                            if isinstance(part, dict) and part.get("type") == "image_url":
                                images.append(part)

                duration_ms = int((time.time() - t0) * 1000)

                if not images:
                    # Hard refusal — model declined for policy reasons. Retry won't help.
                    if refusal:
                        logger.warning("Model refused scene %d [%s]: %s — no retry",
                                       scene_index, style_label, str(refusal)[:200])
                        fire(log_api_call(story_id=story_id, service="openrouter", model=image_model,
                                          purpose="illustration", status="failed", duration_ms=duration_ms,
                                          request_text=prompt_text, error=f"Refused: {str(refusal)[:200]}"))
                        return None
                    logger.warning("No images in response for scene %d [%s] (attempt %d)",
                                   scene_index, style_label, attempt)
                    fire(log_api_call(story_id=story_id, service="openrouter", model=image_model,
                                      purpose="illustration", status="failed", duration_ms=duration_ms,
                                      request_text=prompt_text, error=f"No images (attempt {attempt})"))
                    last_error = "no images"
                    if attempt < IMAGE_MAX_ATTEMPTS:
                        await asyncio.sleep(IMAGE_RETRY_DELAY)
                    continue

                img_url = images[0]
                if isinstance(img_url, dict):
                    img_url = img_url.get("image_url", {}).get("url", "")

                if img_url.startswith("data:"):
                    b64_data = img_url.split(",", 1)[1] if "," in img_url else img_url
                    img_bytes = base64.b64decode(b64_data)
                    fire(log_api_call(story_id=story_id, service="openrouter", model=image_model,
                                      purpose="illustration", status="success", duration_ms=duration_ms,
                                      request_text=prompt_text))
                    if attempt > 1:
                        logger.info("Illustration scene %d succeeded on attempt %d", scene_index, attempt)
                    return img_bytes
                else:
                    logger.warning("Unexpected image format for scene %d [%s] (attempt %d)",
                                   scene_index, style_label, attempt)
                    fire(log_api_call(story_id=story_id, service="openrouter", model=image_model,
                                      purpose="illustration", status="failed", duration_ms=duration_ms,
                                      request_text=prompt_text, error="Unexpected image format"))
                    last_error = "bad format"
                    if attempt < IMAGE_MAX_ATTEMPTS:
                        await asyncio.sleep(IMAGE_RETRY_DELAY)
                    continue

        except Exception as e:
            import traceback
            logger.warning("Image generation exception for scene %d [%s] (attempt %d): %s",
                           scene_index, style_label, attempt, e)
            if attempt == IMAGE_MAX_ATTEMPTS:
                logger.error("Final attempt failed for scene %d [%s]: %s\n%s",
                             scene_index, style_label, e, traceback.format_exc())
            last_error = f"exception: {e}"
            if attempt < IMAGE_MAX_ATTEMPTS:
                await asyncio.sleep(IMAGE_RETRY_DELAY)
            continue

    logger.error("Illustration scene %d [%s] failed after %d attempts (last: %s)",
                 scene_index, style_label, IMAGE_MAX_ATTEMPTS, last_error)
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
    scene_full_text: str = "",
    has_character_sheet: bool = False,
) -> str:
    """Build the text prompt for a single illustration."""
    continuity = ""
    if previous_scene_desc:
        continuity = f"\nPrevious scene showed: {previous_scene_desc}. This scene continues the same story."

    sheet_block = ""
    if has_character_sheet:
        sheet_block = (
            "\n=== CHARACTER REFERENCE SHEET (attached image) ===\n"
            "An image is attached showing the exact look of every character (a reference lineup). "
            "Match each character in this scene to that sheet EXACTLY — same face, hair, "
            "eye color, clothing, accessories, and body proportions.\n"
        )

    # Build appearance block for characters in this scene
    appearance_lines = []
    for char_name in scene.get("characters_present", []):
        desc = character_appearances.get(char_name, "")
        if desc:
            appearance_lines.append(f"  - {char_name}: {desc}")
    appearance_block = ""
    if appearance_lines:
        appearance_block = (
            "\nCHARACTER APPEARANCES (MUST match exactly in every scene):\n"
            + "\n".join(appearance_lines)
        )

    # Full text block for context
    text_block = ""
    if scene_full_text:
        text_block = (
            f"\n=== FULL TEXT OF THIS SCENE (most important — illustrate THIS) ===\n"
            f"{scene_full_text[:1000]}\n"
        )

    return (
        f"=== STYLE (fixed for all scenes) ===\n"
        f"{style_block}\n"
        f"{sheet_block}\n"
        f"=== CHARACTER BIBLE (fixed — do NOT change between scenes) ===\n"
        f"{appearance_block}\n"
        f"Do NOT redesign any character. Keep IDENTICAL: face shape, hair color, hairstyle, "
        f"eye color, clothing colors, accessories, body proportions.\n\n"
        f"=== SCENE (variable — this is what changes) ===\n"
        f"Fairy tale: '{fairy_tale_title}'\n"
        f"Scene {scene_index + 1} of {total_scenes}\n"
        f"Setting: {scene.get('setting', 'forest')}\n"
        f"Mood: {scene.get('mood', 'magical')}\n"
        f"Action: {scene.get('description', '')}\n"
        f"Characters present: {', '.join(scene.get('characters_present', []))}\n"
        f"{text_block}"
        f"{continuity}\n\n"
        f"Generate a NEW unique illustration for this scene with NEW poses and composition. "
        f"Each character appears EXACTLY ONCE. "
        f"{style_suffix}\n{NO_TEXT_RULE}"
    )


def _build_character_sheet_prompt(
    cast: list[tuple[str, str]],
    style_block: str,
    has_photo: bool,
    fairy_tale_title: str,
) -> str:
    """Prompt for a single 'cast lineup' character reference sheet."""
    cast_lines = "\n".join(f"  - {name}: {desc}" for name, desc in cast if desc)
    photo_note = ""
    if has_photo:
        photo_note = (
            "\nThe attached photo is the real child who is the MAIN CHARACTER. "
            "Make that character's face RECOGNIZABLE from the photo — same face shape, "
            "hair color, hairstyle, eye color.\n"
        )
    return (
        f"Create a CHARACTER MODEL SHEET (reference lineup) for the children's fairy tale "
        f"'{fairy_tale_title}'.\n"
        f"Draw ALL the characters below TOGETHER, standing side by side in one row, "
        f"each full body head-to-toe, facing forward, in a neutral relaxed standing pose, "
        f"on a plain light-grey studio background. This is a reference sheet, NOT a story scene — "
        f"no scenery, no props, no action, each character clearly separated.\n\n"
        f"CHARACTERS (draw each EXACTLY ONCE, left to right):\n{cast_lines}\n"
        f"{photo_note}\n"
        f"{style_block}\n\n"
        f"Wide landscape 16:9.\n{NO_TEXT_RULE}"
    )


async def generate_character_sheet(
    cast: list[str],
    character_appearances: dict[str, str],
    reference_photo_b64: str | None = None,
    reference_photos: list[str] | None = None,
    fairy_tale_title: str = "",
    story_id: int = None,
    style_block: str | None = None,
) -> str | None:
    """Render one cast-lineup reference image used to keep all scenes consistent.

    Returns the image as a base64 string (no data: prefix), or None on failure.
    """
    if style_block is None:
        style_block = await _resolve_style_block(DEFAULT_STYLE)
    cast_pairs = [(name, character_appearances.get(name, "")) for name in cast]

    photos = reference_photos or ([reference_photo_b64] if reference_photo_b64 else [])
    image_content = []
    for photo in photos:
        if photo:
            purl = photo if photo.startswith("data:") else f"data:image/jpeg;base64,{photo}"
            image_content.append({"type": "image_url", "image_url": {"url": purl}})

    prompt = _build_character_sheet_prompt(
        cast_pairs, style_block, bool(image_content), fairy_tale_title)
    content = image_content + [{"type": "text", "text": prompt}]
    img_bytes = await _call_image_api(content, -1, "char_sheet", story_id=story_id)
    if img_bytes:
        return base64.b64encode(img_bytes).decode("ascii")
    return None


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
    story_id: int = None,
    previous_illustration_b64: str | None = None,
    scene_full_text: str = "",
    character_sheet_b64: str | None = None,
    style_block: str | None = None,
) -> bytes | None:
    """Generate one scene illustration via Gemini.

    Likeness: when a photo is present we use a forceful "photo is the ground truth"
    identity prompt so the real child is recognisable (the chosen art style is applied
    on top). When there is no photo, an optional character_sheet_b64 (cast lineup) is
    attached as the consistency anchor. Scenes are independent, so the batch runs them
    in parallel.
    """
    if style_block is None:
        style_block = await _resolve_style_block(DEFAULT_STYLE)

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

    # Character reference sheet (cast lineup) — the visual consistency anchor (no-photo path)
    sheet_content = []
    if character_sheet_b64:
        sheet_url = character_sheet_b64
        if not sheet_url.startswith("data:"):
            sheet_url = f"data:image/png;base64,{character_sheet_b64}"
        sheet_content.append({"type": "image_url", "image_url": {"url": sheet_url}})

    if photo_content:
        # Photo present → forceful identity prompt: the photo is ground truth for the child's face.
        # We separate MAIN child appearance (must be repeated verbatim every scene for
        # cross-scene consistency) from SECONDARY characters.
        cast = scene.get("characters_present", [])
        main_name = cast[0] if cast else None
        main_desc = (character_appearances or {}).get(main_name, "") if main_name else ""
        other_lines = []
        for char_name in cast[1:]:
            desc = (character_appearances or {}).get(char_name, "")
            if desc:
                other_lines.append(f"  - {char_name}: {desc}")
        others_block = ("Other characters in the scene:\n" + "\n".join(other_lines) + "\n"
                        if other_lines else "")
        main_block = (
            f"MAIN CHARACTER: {main_name} — {main_desc}.\n"
            "These traits (hair color, length, eye color, age) MUST match across all scenes — "
            "they are the consistency anchor.\n" if main_name and main_desc else ""
        )
        text_block = f"\n\nFull scene text:\n{scene_full_text[:800]}" if scene_full_text else ""
        prompt = (
            "The attached photo is a real specific child — the MAIN CHARACTER of this scene. "
            "Render that child preserving their identity so a parent instantly recognises them. "
            "Study the photo and copy their EXACT face shape, cheeks, eyes and apparent age. "
            "Do NOT age them up or down. "
            "IF the photo clearly shows their hair (length, cut, color) — copy it exactly. "
            "IF the hair is hidden (hat, scarf, hood, profile shot) or unclear — use the MAIN "
            "CHARACTER description below as ground truth for hair and age. NEVER invent a different "
            "hair color or age between scenes — pick one and keep it.\n"
            "If the photo contains other people or held objects, focus ONLY on the main child's "
            "face and ignore everything else.\n\n"
            f"{main_block}"
            f"Scene: {scene.get('description', '')}\n"
            f"Setting: {scene.get('setting', 'forest')}\n"
            f"Mood: {scene.get('mood', 'magical')}\n"
            f"{others_block}{text_block}\n\n"
            f"{style_block}\n\n"
            f"Wide landscape 16:9.\n{NO_TEXT_RULE}"
        )
        # Photo FIRST — the model transforms the real child — then the scene text
        content = photo_content + [{"type": "text", "text": prompt}]
    else:
        # No photo — rely on character sheet (if any) + character bible text
        prompt = _build_scene_prompt(
            scene, scene_index, total_scenes, fairy_tale_title, characters_desc,
            character_appearances or {},
            previous_scene_desc, style_block,
            "",
            scene_full_text=scene_full_text,
            has_character_sheet=bool(sheet_content),
        )
        # Reference sheet FIRST (consistency anchor), then text
        content = sheet_content + [{"type": "text", "text": prompt}]

    img_bytes = await _call_image_api(content, scene_index, "scene", story_id=story_id)

    return img_bytes


async def generate_illustrations_batch(
    screenplay: dict,
    reference_photo_b64: str | None = None,
    reference_photos: list[str] | None = None,
    on_progress=None,
    story_id: int = None,
    on_illustration_ready: Callable[[int, bytes], Awaitable[None]] | None = None,
    timeline_text: str | None = None,
    style: str | None = None,
) -> list[bytes]:
    """Generate all illustrations for a fairy tale in the chosen art style.

    Args:
        style: art style key (painted/watercolor/realistic/pixar). Default: painted.
        on_illustration_ready: Callback fired for each illustration as it's generated.
            Receives (scene_index, image_bytes).

    Returns list of PNG bytes (may contain None for failed scenes).
    """
    style_block = await _resolve_style_block(style)
    has_photo = bool(reference_photo_b64 or (reference_photos and any(reference_photos)))

    # Step 1: Split into scenes (with timeline if available)
    scenes, character_appearances = await split_into_scenes(
        screenplay, story_id=story_id, timeline_text=timeline_text)

    title = screenplay["title"]
    characters_desc = ", ".join(
        f"{c['name']} ({c.get('personality', '')})"
        for c in screenplay["characters"]
        if c["id"] != "narrator"
    )

    # Build character name lookup for full text extraction
    char_names = {c["id"]: c["name"] for c in screenplay.get("characters", [])}
    segments = screenplay.get("segments", [])

    def _scene_text(scene: dict) -> str:
        """Extract the full spoken/narrated text for a scene's segment range."""
        s_start = scene.get("segment_start", 0)
        s_end = scene.get("segment_end", len(segments))
        if not (isinstance(s_start, int) and isinstance(s_end, int)):
            return ""
        lines = []
        for si in range(max(0, s_start), min(s_end, len(segments))):
            seg = segments[si]
            speaker = char_names.get(seg.get("character_id", ""), "")
            text_clean = re.sub(r'\[[\w\s]+\]', '', seg.get("text", "")).strip()
            if text_clean:
                lines.append(f"{speaker}: {text_clean}" if speaker else text_clean)
        return "\n".join(lines)

    # Step 2: Character reference sheet (cast lineup) — the consistency anchor for the
    # NO-PHOTO case. When a photo IS present we deliberately SKIP the sheet: the photo is
    # a far stronger anchor for the child's face, and anchoring to a generic cartoon sheet
    # was shown to drag the child's likeness toward a generic look. Scenes are independent
    # either way, so they still run in parallel. Recurring cast = chars in >=2 scenes.
    character_sheet_b64 = None
    if not has_photo:
        from collections import Counter
        appearance_counts = Counter()
        for sc in scenes:
            for name in sc.get("characters_present", []):
                appearance_counts[name] += 1
        recurring = [n for n, c in appearance_counts.items()
                     if c >= 2 and character_appearances.get(n)]
        if not recurring:
            recurring = [n for n in appearance_counts if character_appearances.get(n)]
        recurring = recurring[:6]

        if recurring:
            if on_progress:
                r = on_progress("🎨 Рисую референс персонажей...")
                if asyncio.iscoroutine(r):
                    await r
            try:
                character_sheet_b64 = await generate_character_sheet(
                    cast=recurring,
                    character_appearances=character_appearances,
                    reference_photo_b64=reference_photo_b64,
                    reference_photos=reference_photos,
                    fairy_tale_title=title,
                    story_id=story_id,
                    style_block=style_block,
                )
            except Exception as e:
                logger.warning("Character sheet generation failed: %s", e)
            logger.info("Character sheet: %s (cast: %s)",
                        "ready" if character_sheet_b64 else "FAILED/skipped",
                        ", ".join(recurring))

    # Step 3: Generate all scene illustrations in parallel (bounded concurrency)
    if on_progress:
        r = on_progress(f"🎨 Рисую {len(scenes)} иллюстраций...")
        if asyncio.iscoroutine(r):
            await r

    sem = asyncio.Semaphore(5)

    async def _gen_one(i: int, scene: dict) -> bytes | None:
        async with sem:
            img = await generate_illustration(
                scene=scene,
                scene_index=i,
                total_scenes=len(scenes),
                reference_photo_b64=reference_photo_b64,
                previous_scene_desc=None,
                fairy_tale_title=title,
                characters_desc=characters_desc,
                character_appearances=character_appearances,
                reference_photos=reference_photos,
                story_id=story_id,
                scene_full_text=_scene_text(scene),
                character_sheet_b64=character_sheet_b64,
                style_block=style_block,
            )
        logger.info("Illustration %d/%d: %s", i + 1, len(scenes),
                    f"{len(img):,}b" if img else "FAILED")
        if img and on_illustration_ready:
            try:
                await on_illustration_ready(i, img)
            except Exception as e:
                logger.warning("on_illustration_ready callback failed for scene %d: %s", i, e)
        return img

    gathered = await asyncio.gather(
        *[_gen_one(i, sc) for i, sc in enumerate(scenes)],
        return_exceptions=True,
    )
    results = []
    for i, r in enumerate(gathered):
        if isinstance(r, BaseException):
            logger.warning("Illustration scene %d raised: %s", i, r)
            results.append(None)
        else:
            results.append(r)

    successful = sum(1 for r in results if r is not None)
    logger.info("Illustrations complete: %d/%d successful", successful, len(results))

    return results, scenes
