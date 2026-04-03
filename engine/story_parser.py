# -*- coding: utf-8 -*-
"""LLM prompts and tag mapping for fairy tale generation."""

SCREENWRITER_PROMPT = """\
Ты — сценарист детских аудиосказок. Напиши короткую сказку (на 3-5 минут чтения вслух) на русском языке.

Информация о ребёнке и теме:
{context}

ФОРМАТ ОТВЕТА — только валидный JSON, без markdown:
{{
  "title": "Название сказки",
  "characters": [
    {{
      "id": "narrator",
      "name": "Рассказчик",
      "gender": "female",
      "age": "middle",
      "role": "narrator",
      "personality": "тёплая, спокойная, увлекающая"
    }},
    {{
      "id": "bear",
      "name": "Медведь",
      "gender": "male",
      "age": "middle",
      "role": "comic",
      "personality": "добрый, неуклюжий, весёлый"
    }}
  ],
  "segments": [
    {{
      "character_id": "narrator",
      "emotion": "cheerful",
      "pace": "normal",
      "text": "В одном старом лесу жил большой бурый Медведь."
    }},
    {{
      "character_id": "bear",
      "emotion": "excited",
      "pace": "fast",
      "text": "Ла-ла-лааа! Какое прекрасное утро!"
    }}
  ],
  "scenes": [
    {{
      "segments": [0, 1, 2],
      "ambient": "forest"
    }}
  ]
}}

ВАЖНО — ПАУЗЫ И ЭМОЦИИ:
Текст озвучивается через ElevenLabs v3. В тексте ОБЯЗАТЕЛЬНО используй audio-теги:
- Паузы: [pause], [long pause] — вставляй между фразами и предложениями. МНОГО пауз!
- Эмоции: [happy], [excited], [sad], [angry], [nervous], [cheerfully]
- Голосовые действия: [laughs], [gasps], [sigh], [breathes]
- Шёпот: [whispers], [speaking softly]
- Темп: [slows down] — используй для драматичных и нежных моментов
- Характер: [childlike tone], [deep voice]
Рассказчик должен говорить МЕДЛЕННО, с частыми паузами — как настоящий рассказчик сказок на ночь.
Между репликами разных персонажей ВСЕГДА ставь [pause] или [long pause].
Каждый сегмент рассказчика должен начинаться с [slows down] или содержать [pause].

ПРАВИЛА:
1. Персонаж "narrator" обязателен — он ведёт историю между диалогами
2. Каждый сегмент — один говорящий, максимум 200 символов текста
3. 15-25 сегментов на сказку
4. Язык — живой, детский русский
5. emotion: neutral, cheerful, excited, nervous, sad, angry, whisper, soft, mysterious
6. pace: slow, normal, fast
7. ambient: forest, cave, stream, night, sea, village, castle, sky
8. role для персонажей: narrator, hero, villain, wise, comic, magical
9. gender: male, female
10. age: child, young, middle, elderly
11. Сказка должна быть увлекательной, с моралью и счастливым концом
12. Индексы в scenes.segments — это индексы массива segments (начиная с 0)
"""

EMOTION_TO_TAGS = {
    "neutral":    "",
    "cheerful":   "[cheerfully]",
    "excited":    "[happy] [excited]",
    "nervous":    "[nervous]",
    "sad":        "[sad]",
    "angry":      "[angry]",
    "whisper":    "[whispers]",
    "soft":       "[speaking softly]",
    "mysterious": "[whispers] [slows down]",
}

PACE_TO_TAGS = {
    "slow":   "[slows down]",
    "normal": "",
    "fast":   "",
}

AMBIENT_MAP = {
    "forest":  "forest_ambience.mp3",
    "cave":    "cave_ambience.mp3",
    "stream":  "stream_water.mp3",
    "night":   "night_forest.mp3",
    "sea":     "forest_ambience.mp3",
    "village": "forest_ambience.mp3",
    "castle":  "cave_ambience.mp3",
    "sky":     "forest_ambience.mp3",
}


def build_tagged_text(text: str, emotion: str, pace: str, is_narrator: bool = False) -> str:
    """Add ElevenLabs v3 audio tags based on emotion and pace.

    If is_narrator, always prepend [slows down] for calmer storytelling pace.
    """
    tags = []

    # Narrator always speaks slowly
    if is_narrator and pace != "fast":
        tags.append("[slows down]")

    pace_tag = PACE_TO_TAGS.get(pace, "")
    if pace_tag and pace_tag not in tags:
        tags.append(pace_tag)

    emotion_tag = EMOTION_TO_TAGS.get(emotion, "")
    if emotion_tag:
        tags.append(emotion_tag)

    prefix = " ".join(tags)
    return f"{prefix} {text}" if prefix else text
