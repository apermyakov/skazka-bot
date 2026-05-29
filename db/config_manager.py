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
                    # asyncpg returns JSONB strings with quotes — parse them
                    if isinstance(raw, str):
                        try:
                            raw = json.loads(raw)
                        except (json.JSONDecodeError, TypeError):
                            pass
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
    "prompt.story_text": (
        "Ты — детский писатель. Напиши волшебную сказку на ночь на русском языке.\n\nИнформация о ребёнке и теме:\n{context}\n\nПРАВИЛА:\n1. Напиши полноценную сказку на 10-15 минут чтения вслух\n2. Первая строка — ЗАГОЛОВОК: название сказки\n3. Затем пустая строка и текст сказки\n4. Используй диалоги — указывай имя говорящего перед репликой\n5. Рассказчик ведёт историю между диалогами\n6. Язык — тёплый, живой, детский русский\n7. 2-4 персонажа (включая главного героя-ребёнка из входных данных)\n8. Сказка должна быть доброй, с мягкой моралью и счастливым концом\n9. Избегай жестокости, страха и нравоучений\n10. Пиши подробно: описывай обстановку, чувства, действия\n\nФОРМАТ ДИАЛОГОВ:\nРассказчик: текст описания\nИмяПерсонажа: реплика персонажа\n\nНапиши только текст сказки, без комментариев.",
        "prompt", "Промпт для генерации текста сказки (без JSON)"
    ),
    "prompt.story_text_system": (
        "Ты — талантливый детский писатель. Пиши только текст сказки.",
        "prompt", "System prompt для генерации текста сказки"
    ),
    "prompt.screenplay_convert": (
        "Преобразуй текст сказки в структурированный JSON для озвучки.\n\nТекст сказки:\n{text}\n\nВерни ТОЛЬКО валидный JSON без markdown:\n{{\n  \"title\": \"{title}\",\n  \"characters\": [\n    {{\n      \"id\": \"narrator\",\n      \"name\": \"Рассказчик\",\n      \"gender\": \"female\",\n      \"age\": \"middle\",\n      \"role\": \"narrator\",\n      \"personality\": \"тёплая и спокойная\"\n    }}\n  ],\n  \"segments\": [\n    {{\n      \"character_id\": \"narrator\",\n      \"emotion\": \"cheerful\",\n      \"pace\": \"slow\",\n      \"text\": \"[slows down] [pause] Текст сегмента.\"\n    }}\n  ],\n  \"scenes\": [\n    {{\n      \"segments\": [0, 1, 2],\n      \"ambient\": \"forest\"\n    }}\n  ]\n}}\n\nПРАВИЛА:\n1. Персонаж narrator ОБЯЗАТЕЛЕН — он ведёт историю\n2. Для каждого говорящего персонажа создай запись в characters с id, name, gender, age, role\n3. Раздели текст на сегменты — каждый сегмент один говорящий, максимум 200 символов\n4. Добавь audio-теги ElevenLabs v3 в текст сегментов:\n   - [pause], [long pause] — между фразами\n   - [happy], [excited], [sad], [angry], [whispers] — эмоции\n   - [slows down] — для рассказчика\n   - [laughs], [gasps], [sigh] — действия\n5. emotion: neutral, cheerful, excited, nervous, sad, angry, whisper, soft, mysterious\n6. pace: slow, normal, fast\n7. role: narrator, hero, villain, wise, comic, magical\n8. gender: male, female\n9. age: child, young, middle, elderly\n10. ambient: forest, cave, stream, night, sea, ocean, rain, storm, fire, wind, birds, meadow, garden, city, castle, magic, sky, space, snow\n11. Сцены покрывают ВСЕ сегменты\n12. Рассказчик: pace=slow, начинай с [slows down]",
        "prompt", "Промпт конвертации текста в screenplay JSON"
    ),
    "prompt.screenplay_convert_system": (
        "Ты генерируешь ТОЛЬКО валидный JSON. Никакого текста до или после JSON.",
        "prompt", "System prompt для конвертации в screenplay"
    ),
    "prompt.screenwriter": (
        "Ты — сценарист детских аудиосказок. Напиши короткую сказку (на 3-5 минут чтения вслух) на русском языке.\n\nИнформация о ребёнке и теме:\n{context}\n\nФОРМАТ ОТВЕТА — только валидный JSON, без markdown:\n{{\n  \"title\": \"Название сказки\",\n  \"characters\": [\n    {{\n      \"id\": \"narrator\",\n      \"name\": \"Рассказчик\",\n      \"gender\": \"female\",\n      \"age\": \"middle\",\n      \"role\": \"narrator\",\n      \"personality\": \"тёплая, спокойная, увлекающая\"\n    }}\n  ],\n  \"segments\": [\n    {{\n      \"character_id\": \"narrator\",\n      \"emotion\": \"cheerful\",\n      \"pace\": \"normal\",\n      \"text\": \"В одном старом лесу жил большой бурый Медведь.\"\n    }}\n  ],\n  \"scenes\": [\n    {{\n      \"segments\": [0, 1, 2],\n      \"ambient\": \"forest\"\n    }}\n  ]\n}}\n\nВАЖНО — ПАУЗЫ И ЭМОЦИИ:\nТекст озвучивается через ElevenLabs v3. В тексте ОБЯЗАТЕЛЬНО используй audio-теги:\n- Паузы: [pause], [long pause]\n- Эмоции: [happy], [excited], [sad], [angry], [nervous], [cheerfully]\n- Голосовые действия: [laughs], [gasps], [sigh], [breathes]\n- Шёпот: [whispers], [speaking softly]\n- Темп: [slows down]\n- Характер: [childlike tone], [deep voice]\nРассказчик должен говорить МЕДЛЕННО, с частыми паузами.\n\nПРАВИЛА:\n1. Персонаж \"narrator\" обязателен\n2. Каждый сегмент — один говорящий, максимум 200 символов текста\n3. 15-25 сегментов на сказку\n4. Язык — живой, детский русский\n5. emotion: neutral, cheerful, excited, nervous, sad, angry, whisper, soft, mysterious\n6. pace: slow, normal, fast\n7. ambient: forest, cave, stream, night, sea, ocean, rain, storm, fire, fireplace, wind, birds, meadow, garden, village, city, market, castle, dungeon, magic, sky, space, snow, winter\n8. role: narrator, hero, villain, wise, comic, magical\n9. gender: male, female\n10. age: child, young, middle, elderly\n11. Сказка должна быть увлекательной, с моралью и счастливым концом\n12. Индексы в scenes.segments — это индексы массива segments (начиная с 0)",
        "prompt", "Промпт сценариста для генерации сказки"
    ),
    "prompt.screenwriter_system": (
        "Ты генерируешь ТОЛЬКО валидный JSON. Никакого текста до или после JSON.",
        "prompt", "System prompt для LLM при генерации сценария"
    ),
    "prompt.scene_split": (
        "Ты — режиссёр раскадровки детской аудиосказки. Озвучка УЖЕ записана.\nКаждая строка пронумерована [i] и помечена таймкодом [at Xs, dur Ys] — это КОГДА и сколько звучит сегмент.\n\nСценарий:\nНазвание: {title}\nПерсонажи: {characters}\nТаймлайн:\n{story_text}\n\nСоставь РАСКАДРОВКУ — последовательность кадров (иллюстраций), синхронных с озвучкой.\n\nГЛАВНОЕ ПРАВИЛО: картинка кадра появляется на экране РОВНО на той строке, где ВПЕРВЫЕ произносится то, что на ней изображено — НИКОГДА не раньше. Поэтому:\n- segment_start кадра = номер сегмента, где это визуальное событие впервые озвучивается;\n- description описывает то, что видно ИМЕННО В ЭТОТ момент (что нового только что ввёл рассказчик), а НЕ кульминацию блока и не то, что будет дальше.\nКадр держится на экране до следующего кадра.\n\nВерни ТОЛЬКО JSON без markdown:\n{{\n  \"character_appearances\": {{\n    \"имя_персонажа\": \"внешность: цвет волос/шерсти, глаз, одежда\"\n  }},\n  \"scenes\": [\n    {{\n      \"scene_index\": 0,\n      \"segment_start\": 0,\n      \"description\": \"что видно В ЭТОТ момент (макс 12 слов)\",\n      \"characters_present\": [\"имя1\"],\n      \"setting\": \"лес\",\n      \"mood\": \"спокойный\"\n    }}\n  ]\n}}\n\nПРАВИЛА:\n1. Кадр 0 ОБЯЗАТЕЛЬНО имеет segment_start=0 (открывающая сцена).\n2. Новый кадр — ТОЛЬКО когда визуальная ситуация заметно меняется: новое место, появляется важный персонаж/предмет, ключевой поворот. НЕ создавай похожие кадры в одной обстановке. ВАЖНО: когда ВПЕРВЫЕ появляется персонаж или предмет (мишка, колокольчик и т.п.), привяжи кадр к сегменту, где о нём СКАЗАЛИ впервые, и НЕ показывай его в более ранних кадрах.\n3. characters_present — только те, кто УЖЕ в кадре на момент segment_start. НЕ добавляй тех, кто появится позже в этом отрезке: персонаж не должен быть виден на картинке раньше, чем о нём сказали.\n4. segment_start строго возрастает; кадры покрывают всю сказку до конца.\n5. 4-6 кадров на сказку. Соседние кадры — не ближе ~25 секунд по таймкоду (смотри [at Xs]), иначе картинки мелькают и выглядят как дубли.\n6. description — что видно В МОМЕНТ segment_start, МАКСИМУМ 12 слов. НЕ описывай будущее или развязку — только текущий момент.\n7. Главный герой-ребёнок присутствует в кадрах, где он есть по сюжету.\n8. character_appearances ОБЯЗАТЕЛЕН — опиши внешность КАЖДОГО персонажа (кроме рассказчика).\n9. Если в тексте указан цвет (серый кот, рыжая лиса) — ОБЯЗАТЕЛЬНО укажи этот цвет.",
        "prompt", "Промпт раскадровки: привязка кадров к таймкодам озвучки"
    ),
    "prompt.style_pixar": (
        "Generate a wide landscape (16:9) Pixar-style 3D cartoon illustration. The character must be RECOGNIZABLE from the reference photo. STRICTLY NO text, words, letters, signs, or writing anywhere. Anatomically correct: exactly two arms, two hands per person. Each animal has exactly ONE head, ONE body, and the correct number of legs for its species. NEVER duplicate or merge animals — if the scene has one cat, draw exactly ONE cat. Warm, magical lighting. Rich, vibrant colors. Consistent style and color palette throughout the series.",
        "prompt", "Стиль Pixar для иллюстраций"
    ),
    "prompt.style_kids_drawing": (
        "Generate a wide landscape (16:9) illustration in the style of a high-quality children's book watercolor drawing. Hand-drawn feel with soft watercolor textures, gentle pencil outlines, and pastel colors. Like a beautiful illustration from a premium children's picture book — warm, cozy, slightly whimsical. NOT crude or messy — this is professional children's book art with a hand-crafted feel. STRICTLY NO text, words, letters, signs, or writing anywhere. Soft, dreamy lighting. Gentle watercolor palette.",
        "prompt", "Стиль детской акварели для иллюстраций"
    ),
    "prompt.style.painted": (
        "Generate a wide landscape (16:9) classic hand-PAINTED fairy-tale storybook illustration — rich gouache and oil painting with visible brushwork, warm golden light, painterly textures, the timeless look of a treasured children's picture book. Painterly, not flat vector, not photographic. The main child character must be RECOGNIZABLE from the reference photo. STRICTLY NO text, words, letters, signs, or writing anywhere. Anatomically correct: exactly two arms, two hands per person. Each animal has exactly ONE head, ONE body, and the correct number of legs for its species. NEVER duplicate or merge animals — if the scene has one cat, draw exactly ONE cat. Warm, magical lighting. Consistent painterly style and palette throughout the series.",
        "prompt", "Стиль 'Живопись' (storybook gouache/oil) — дефолтный"
    ),
    "prompt.style.realistic": (
        "Generate a wide landscape (16:9) polished SEMI-REALISTIC 3D render with realistic facial proportions, skin and detail (close to a real child's face while still gentle and animated), soft cinematic lighting, high detail. Prioritise faithful real facial likeness from the reference photo over cartoon stylisation. STRICTLY NO text, words, letters, signs, or writing anywhere. Anatomically correct: exactly two arms, two hands per person. Each animal has exactly ONE head, ONE body, and the correct number of legs for its species. NEVER duplicate or merge animals — if the scene has one cat, draw exactly ONE cat. Warm, magical lighting. Consistent style throughout the series.",
        "prompt", "Стиль 'Реализм' (semi-realistic 3D)"
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
    "model.llm": ("google/gemini-2.5-flash", "model", "Модель для генерации сценария"),
    "model.llm_fallback": ("google/gemini-2.5-flash", "model", "Быстрая стабильная модель-фолбэк, если основная тормозит/падает"),
    "llm.call_timeout_sec": (75, "llm", "Таймаут одного вызова LLM (сек) до перехода на фолбэк"),
    "model.image": ("google/gemini-2.5-flash-image", "model", "Модель для генерации иллюстраций"),
    "model.tts": ("eleven_v3", "model", "Модель ElevenLabs TTS"),
    "model.transcribe": ("google/gemini-2.5-flash", "model", "Модель для транскрипции голоса"),
    "image.default_style": ("painted", "image", "Стиль иллюстраций по умолчанию: painted|watercolor|realistic|pixar"),
    "image.size": ("1K", "image", "Разрешение генерации картинок: 1K (для 720p, дёшево) | 2K | 4K"),
    "limit.stories_per_day": (2, "limit", "Лимит сказок в день на пользователя (админы без лимита)"),
    "pricing.story_rub": (999, "pricing", "Цена одной сказки на сайте (рубли)"),
    "yukassa.vat_code": (1, "yukassa", "Код НДС для чека: 1=Без НДС, 2=0%, 3=10%, 4=20%, 5=10/110, 6=20/120"),
    "bot.payment_enabled": (False, "pricing", "Платная озвучка в Telegram-боте через YuKassa. False = бот бесплатный"),
    "bot.payment_return_url": ("https://t.me/SkazikBot", "pricing", "Куда вернуть пользователя после оплаты в боте"),

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
    "audio.tempo_slow": (1.00, "audio", "Темп при выборе 'Медленно' (множитель)"),
    "audio.tempo_normal": (1.15, "audio", "Темп при выборе 'Нормально' (множитель)"),
    "audio.tempo_fast": (1.30, "audio", "Темп при выборе 'Быстро' (множитель)"),

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
