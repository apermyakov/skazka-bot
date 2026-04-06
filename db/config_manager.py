# -*- coding: utf-8 -*-
"""Dynamic configuration from PostgreSQL with in-memory cache.

Usage:
    from db.config_manager import cfg
    value = await cfg.get("llm.screenplay_temperature", 0.8)

Values are cached for 30 seconds. Change in TablePlus → picked up automatically.
"""

import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class ConfigManager:
    TTL = 30  # seconds between DB reloads

    def __init__(self):
        self._cache: dict[str, Any] = {}
        self._loaded_at: float = 0
        self._pool = None

    def set_pool(self, pool):
        self._pool = pool

    async def get(self, key: str, default: Any = None) -> Any:
        """Get config value. Auto-reloads from DB every TTL seconds."""
        if time.time() - self._loaded_at > self.TTL:
            await self._reload()
        return self._cache.get(key, default)

    async def _reload(self):
        """Reload all config from DB into cache."""
        if not self._pool:
            return
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch("SELECT key, value FROM config")
                new_cache = {}
                for row in rows:
                    raw = row["value"]
                    # asyncpg returns JSONB as Python objects already
                    new_cache[row["key"]] = raw
                self._cache = new_cache
                self._loaded_at = time.time()
        except Exception as e:
            logger.warning("Config reload failed: %s", e)
            # Keep old cache on failure

    async def seed_defaults(self):
        """Insert default values for keys that don't exist yet."""
        if not self._pool:
            return

        for key, (value, category, description) in DEFAULTS.items():
            try:
                async with self._pool.acquire() as conn:
                    await conn.execute("""
                        INSERT INTO config (key, value, category, description)
                        VALUES ($1, $2::jsonb, $3, $4)
                        ON CONFLICT (key) DO NOTHING
                    """, key, json.dumps(value, ensure_ascii=False), category, description)
            except Exception as e:
                logger.warning("Config seed failed for %s: %s", key, e)

        await self._reload()
        logger.info("Config seeded: %d keys in DB", len(self._cache))


# Singleton
cfg = ConfigManager()


# ── All default values ──
# Format: key → (value, category, description)

DEFAULTS = {
    # ── Prompts ──
    "prompt.screenwriter": (
        "Ты — сценарист детских аудиосказок. Напиши короткую сказку (на 3-5 минут чтения вслух) на русском языке.\n\nИнформация о ребёнке и теме:\n{context}\n\nФОРМАТ ОТВЕТА — только валидный JSON, без markdown:\n{{\n  \"title\": \"Название сказки\",\n  \"characters\": [\n    {{\n      \"id\": \"narrator\",\n      \"name\": \"Рассказчик\",\n      \"gender\": \"female\",\n      \"age\": \"middle\",\n      \"role\": \"narrator\",\n      \"personality\": \"тёплая, спокойная, увлекающая\"\n    }}\n  ],\n  \"segments\": [\n    {{\n      \"character_id\": \"narrator\",\n      \"emotion\": \"cheerful\",\n      \"pace\": \"normal\",\n      \"text\": \"В одном старом лесу жил большой бурый Медведь.\"\n    }}\n  ],\n  \"scenes\": [\n    {{\n      \"segments\": [0, 1, 2],\n      \"ambient\": \"forest\"\n    }}\n  ]\n}}\n\nВАЖНО — ПАУЗЫ И ЭМОЦИИ:\nТекст озвучивается через ElevenLabs v3. В тексте ОБЯЗАТЕЛЬНО используй audio-теги:\n- Паузы: [pause], [long pause]\n- Эмоции: [happy], [excited], [sad], [angry], [nervous], [cheerfully]\n- Голосовые действия: [laughs], [gasps], [sigh], [breathes]\n- Шёпот: [whispers], [speaking softly]\n- Темп: [slows down]\n- Характер: [childlike tone], [deep voice]\nРассказчик должен говорить МЕДЛЕННО, с частыми паузами.\n\nПРАВИЛА:\n1. Персонаж \"narrator\" обязателен\n2. Каждый сегмент — один говорящий, максимум 200 символов текста\n3. 15-25 сегментов на сказку\n4. Язык — живой, детский русский\n5. emotion: neutral, cheerful, excited, nervous, sad, angry, whisper, soft, mysterious\n6. pace: slow, normal, fast\n7. ambient: forest, cave, stream, night, sea, ocean, rain, storm, fire, fireplace, wind, birds, meadow, garden, village, city, market, castle, dungeon, magic, sky, space, snow, winter\n8. role: narrator, hero, villain, wise, comic, magical\n9. gender: male, female\n10. age: child, young, middle, elderly\n11. Сказка должна быть увлекательной, с моралью и счастливым концом\n12. Индексы в scenes.segments — это индексы массива segments (начиная с 0)",
        "prompt", "Промпт сценариста для генерации сказки"
    ),
    "prompt.screenwriter_system": (
        "Ты генерируешь ТОЛЬКО валидный JSON. Никакого текста до или после JSON.",
        "prompt", "System prompt для LLM при генерации сценария"
    ),
    "prompt.scene_split": (
        "Ты — художественный редактор детской книги. Дан сценарий аудиосказки.\nКаждая строка текста пронумерована [0], [1], [2]... — это номера сегментов.\nРаздели сценарий на 7-8 ключевых сцен для иллюстраций.\n\nСценарий:\nНазвание: {title}\nПерсонажи: {characters}\nТекст:\n{story_text}\n\nВерни ТОЛЬКО JSON без markdown:\n{{\n  \"character_appearances\": {{\n    \"имя_персонажа\": \"внешность: цвет волос/шерсти, глаз, одежда\"\n  }},\n  \"scenes\": [\n    {{\n      \"scene_index\": 0,\n      \"segment_start\": 0,\n      \"segment_end\": 3,\n      \"description\": \"Что происходит визуально (макс 10 слов)\",\n      \"characters_present\": [\"имя1\"],\n      \"setting\": \"лес\",\n      \"mood\": \"радостный\"\n    }}\n  ]\n}}\n\nПРАВИЛА:\n1. Ровно 7-8 сцен\n2. segment_start и segment_end — диапазон номеров сегментов\n3. Сцены покрывают ВСЕ сегменты без пропусков\n4. Первая сцена — начало, последняя — счастливый финал\n5. Описание сцены — МАКСИМУМ 10 слов\n6. Главный герой-ребёнок присутствует в каждой сцене\n7. character_appearances ОБЯЗАТЕЛЕН\n8. Если в тексте указан цвет — ОБЯЗАТЕЛЬНО укажи",
        "prompt", "Промпт для разбивки сценария на сцены для иллюстраций"
    ),
    "prompt.style_pixar": (
        "Generate a wide landscape (16:9) Pixar-style 3D cartoon illustration. The character must be RECOGNIZABLE from the reference photo. STRICTLY NO text, words, letters, signs, or writing anywhere. Anatomically correct: exactly two arms, two hands per person. Each animal has exactly ONE head, ONE body, and the correct number of legs for its species. NEVER duplicate or merge animals — if the scene has one cat, draw exactly ONE cat. Warm, magical lighting. Rich, vibrant colors. Consistent style and color palette throughout the series.",
        "prompt", "Стиль Pixar для иллюстраций"
    ),
    "prompt.style_kids_drawing": (
        "Generate a wide landscape (16:9) illustration in the style of a high-quality children's book watercolor drawing. Hand-drawn feel with soft watercolor textures, gentle pencil outlines, and pastel colors. Like a beautiful illustration from a premium children's picture book — warm, cozy, slightly whimsical. NOT crude or messy — this is professional children's book art with a hand-crafted feel. STRICTLY NO text, words, letters, signs, or writing anywhere. Soft, dreamy lighting. Gentle watercolor palette.",
        "prompt", "Стиль детской акварели для иллюстраций"
    ),
    "prompt.face_suffix_single": (
        "The main child character MUST closely match the child in the reference photo: same face shape, hair color, hair style, eye color, skin tone.",
        "prompt", "Промпт для face matching (одно фото)"
    ),
    "prompt.face_suffix_multi": (
        "Reference: {count} photos of the same child from different angles. The main child character MUST closely match this child: same face shape, hair color, hair style, eye color, skin tone. Study ALL reference photos carefully to capture the child's true appearance.",
        "prompt", "Промпт для face matching (несколько фото)"
    ),
    "prompt.transcription": (
        "Расшифруй это голосовое сообщение на русском языке. Это запрос на создание детской сказки — особенно внимательно расшифруй имена детей, возраст и названия. Верни ТОЛЬКО точный текст расшифровки, без комментариев и пояснений.",
        "prompt", "Промпт транскрипции голосового сообщения"
    ),

    # ── Models ──
    "model.llm": ("google/gemini-2.5-flash-preview-04-17", "model", "Модель для генерации сценария"),
    "model.image": ("google/gemini-2.5-flash-image", "model", "Модель для генерации иллюстраций"),
    "model.tts": ("eleven_v3", "model", "Модель ElevenLabs TTS"),
    "model.transcribe": ("google/gemini-2.5-flash-preview-04-17", "model", "Модель для транскрипции голоса"),

    # ── LLM parameters ──
    "llm.screenplay_temperature": (0.8, "llm", "Температура генерации сценария"),
    "llm.screenplay_max_tokens": (8000, "llm", "Max tokens для сценария"),
    "llm.scene_split_temperature": (0.5, "llm", "Температура scene split"),
    "llm.scene_split_max_tokens": (8000, "llm", "Max tokens для scene split"),
    "llm.transcribe_temperature": (0.1, "llm", "Температура транскрипции"),
    "llm.transcribe_max_tokens": (500, "llm", "Max tokens для транскрипции"),
    "llm.story_text_max_chars": (3000, "llm", "Макс символов текста сказки для scene split"),

    # ── TTS ──
    "tts.default_stability": (0.45, "tts", "Стабильность голоса по умолчанию"),
    "tts.default_similarity": (0.80, "tts", "Similarity boost по умолчанию"),
    "tts.default_style": (0.25, "tts", "Style по умолчанию"),
    "tts.language_code": ("ru", "tts", "Код языка для TTS"),

    # ── Audio mixing ──
    "audio.short_pause_sec": (0.7, "audio", "Пауза между сегментами одного говорящего (сек)"),
    "audio.long_pause_sec": (1.3, "audio", "Пауза при смене говорящего (сек)"),
    "audio.ambient_volume": (0.10, "audio", "Громкость фонового звука (0.0-1.0)"),
    "audio.ambient_tail_sec": (5.0, "audio", "Длительность затухания амбиента в конце (сек)"),
    "audio.ambient_fade_in_sec": (1.0, "audio", "Длительность fade-in амбиента (сек)"),
    "audio.default_ambient": ("forest", "audio", "Амбиент по умолчанию"),

    # ── Video ──
    "video.width": (1920, "video", "Ширина видео"),
    "video.height": (1080, "video", "Высота видео"),
    "video.fps": (2, "video", "FPS для слайд-шоу"),
    "video.crf": (18, "video", "CRF качество (ниже = лучше)"),

    # ── Voice scoring ──
    "voice.score_weights": ([0.3, 0.5, 0.2], "voice", "Веса скоринга: [age, tone, role_bonus]"),
    "voice.child_deep_penalty": (0.2, "voice", "Штраф за deep/authoritative голос для детей"),
    "voice.child_bright_bonus": (1.3, "voice", "Бонус за bright/soft/squeaky для детей"),
    "voice.animal_tone_bonus": (1.3, "voice", "Бонус за squeaky/gruff/raspy для животных"),
    "voice.already_used_penalty": (0.5, "voice", "Штраф за повторное использование голоса"),

    # ── UI ──
    "ui.sticker_generation": (
        "CAACAgEAAxUAAWnUJVEkOcUGvclrW1NRjLNvU-L_AAJwBAAChoMgREmYf7NqHL4KOwQ",
        "ui", "Стикер при начале генерации (file_id)"
    ),
    "ui.audio_performer": ("Сказка на ночь", "ui", "Исполнитель в метаданных аудио"),
}
