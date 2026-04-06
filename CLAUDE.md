# Skazka Bot

Telegram-бот для генерации персонализированных аудиосказок с иллюстрациями.

## Архитектура

```
Telegram (aiogram3) → LLM сценарий → Voice assignment → ElevenLabs TTS → ffmpeg mix
                                                        → Gemini Flash Image (иллюстрации)
                                                        ↓
                                          Доставка: MP3 → картинки по одной → MP4 (Ken Burns)
```

Ключевые модули:
- `bot/` — Telegram handlers, FSM, keyboards
- `bot/handlers/create.py` — основной флоу создания сказки, callback-доставка
- `engine/pipeline.py` — оркестрация: TTS + картинки параллельно, callbacks
- `engine/image_generator.py` — Pixar-стиль иллюстрации через Gemini 2.5 Flash Image
- `engine/voice_pool.py` — ~58 голосов ElevenLabs, scoring по gender/age/role/tone
- `engine/tts_client.py` — ElevenLabs TTS с батчингом
- `engine/audio_mixer.py` — ffmpeg: concat сегментов, амбиент, MP4 видео с Ken Burns анимацией
- `engine/llm_client.py` — генерация сценария через Gemini 2.5 Pro
- `engine/story_parser.py` — аудио-теги ElevenLabs v3 для TTS, маппинг амбиентов
- `engine/transcribe.py` — голосовой ввод через Gemini Flash
- `assets/ambient_sounds/` — 4 фоновых звука (forest, cave, stream, night)

## API и ключи (.env)

| Сервис | Переменная | Модель | Для чего |
|--------|-----------|--------|----------|
| Telegram | BOT_TOKEN | — | @SkazikBot |
| OpenRouter | OPENROUTER_API_KEY | `google/gemini-2.5-pro-preview-03-25` | Сценарий + разбивка на сцены |
| OpenRouter | (тот же ключ) | `google/gemini-2.5-flash-image` | Иллюстрации (Pixar-стиль) |
| OpenRouter | (тот же ключ) | `google/gemini-2.5-flash` | Транскрипция голосовых |
| ElevenLabs | ELEVENLABS_API_KEY | v3 API (`eleven_v3`) | TTS, ~58 голосов. План Pro, 500K символов/мес |
| ElevenLabs | ELEVENLABS_PROXY | — | SOCKS5 прокси (гео-блок из РФ) |

## Ограничения

- ElevenLabs v3: max 3000 символов/запрос, мы шлём по ~250 символов (1 сегмент = 1 голос, лимит `segment_char_limit=250`)
- Gemini 2.5 Flash Image: ~10-15 сек на картинку
- MAX_CONCURRENT_TTS=10 (Pro план ElevenLabs)
- Сказка = ~20 сегментов, ~2700 символов, ~3 мин аудио, 7-8 иллюстраций
- Общее время генерации: ~3-5 мин (TTS ~30с, картинки ~100с, видео ~40с)

## Пользовательский флоу

1. `/start` → «Создать сказку»
2. Голос/текст — всё о ребёнке одним сообщением
3. Подтверждение ввода → «Сочинить сказку»
4. Текст сказки → «Озвучить» / «Изменить» / «Заново»
5. Запрос фото ребёнка (или пропуск). Фото может содержать родителей — промпт инструктирует модель выбрать ребёнка
6. Генерация: TTS + картинки параллельно
7. Доставка потоковая:
   - MP3 отправляется сразу после сведения (callback `on_audio_ready`)
   - Картинки приходят по одной по мере генерации (callback `on_illustration_ready`)
   - `asyncio.Event` гарантирует: картинки только ПОСЛЕ MP3
   - MP4 видео с Ken Burns анимацией в конце
8. Фидбек → «Создать ещё»

FSM-состояния: `waiting_topic` → `confirming_input` → `reviewing_story` → `waiting_edits` → `waiting_photo` → `generating`

## Pipeline: callback-архитектура

```python
generate_fairytale(
    context, screenplay, reference_photo_b64,
    on_status,              # str → обновление статус-сообщения
    on_audio_ready,         # dict → MP3 готов, отправить пользователю
    on_illustration_ready,  # (idx, path) → картинка готова, отправить
)
```

TTS и иллюстрации запускаются параллельно (`asyncio.create_task`).
`audio_ready_event = asyncio.Event()` предотвращает отправку картинок до MP3.

## Иллюстрации

- Модель: `google/gemini-2.5-flash-image` (через OpenRouter)
- Стиль: Pixar 3D (16:9, 2K)
- 7-8 сцен на сказку, генерируются последовательно (для визуальной консистентности)
- `character_appearances` — LLM описывает внешность каждого персонажа один раз,
  затем описание инжектится в промпт КАЖДОЙ сцены (чтобы кот не менял цвет)
- Промпт включает: антидупликацию животных, continuity с предыдущей сценой
- Фото ребёнка: промпт умеет выбирать ребёнка из групповых фото (игнорирует взрослых)
- Graceful degradation: если картинки упали — отдаём MP3 без них
- Видео: Ken Burns анимация (6 паттернов zoom/pan чередуются по сценам)
- Scene split: до 5 retry при обрезанном JSON, авто-починка незакрытых скобок

## Voice Pool

~58 голосов в `engine/voice_pool.py`:
- 20 русскоязычных женских (narrator, hero, magical, comic, deep, elderly)
- 18 русскоязычных мужских (narrator, hero, villain, comic, elderly)
- 15 character/animation голосов (child, animal, magical, gruff, squeaky)
- 5 женских villain/special

Автоматический подбор через scoring: gender × age × role × tone × priority.

Scoring-правила:
- `_AGE_SCORE`: таблица совместимости возрастов (child→child=1.0, child→young=0.8, child→middle=0.2)
- `_ROLE_TONE_SCORE`: таблица совместимости ролей и тонов (villain→deep=1.0, comic→bright=1.0)
- `role_bonus`: +0.3 если роль в `best_for` голоса
- `priority`: 1.3× для проверенных best-in-class голосов (★)
- Для `age: "child"`: ×0.2 штраф за deep/authoritative, ×1.3 бонус за bright/soft/squeaky
- Для `role: "animal"`: ×1.3 бонус за squeaky/gruff/raspy
- Уже использованные голоса: ×0.5 (разнообразие)

TTS-параметры по умолчанию: stability=0.45, similarity=0.80, style=0.25.

## Команды

```bash
# Локально
cd skazka_bot && python -m bot

# Деплой (сервер 95.216.117.49, Docker)
ssh root@95.216.117.49
cd /opt/skazka-bot && git pull && docker compose up -d --build
docker logs skazka-bot --tail 50
docker logs skazka-bot -f  # realtime

# Тест callback-порядка (1 сказка)
docker compose exec skazka-bot python test_callbacks.py

# Полный тест (3 сказки)
docker compose exec skazka-bot python test_e2e.py

# Тест только иллюстраций (без LLM/TTS)
docker compose exec skazka-bot python test_illustrations.py
docker compose exec skazka-bot python test_illustrations.py photo.jpg  # с фото
```

## Что нельзя ломать

- Флоу подтверждений: пользователь ВСЕГДА подтверждает перед генерацией
- Голосовой ввод: транскрипция через Gemini Flash, показ результата пользователю
- Паузы между сегментами: 0.7с (один голос), 1.3с (смена голоса)
- 5 сек затухающий амбиент в конце каждой сказки
- Порядок доставки: MP3 ПЕРВЫМ, затем картинки, затем MP4
- Graceful degradation: если картинки упали — отдаём MP3 без них
- `asyncio.Event` между audio и illustrations — не убирать

## Стиль кода

- Python 3.12, async/await, aiohttp
- Пути через pathlib
- Логирование через logging (не print)
- Эмодзи в сообщениях пользователю, но НЕ в логах (Windows cp1251 ломается)

## Известные ограничения и TODO

- Генеративные модели иногда дублируют/искажают персонажей на иллюстрациях
- Нет кэширования сценариев / голосов между сессиями
- Нет системы оплаты / лимитов на пользователя
- Groq API key в конфиге, но нигде не используется (резерв)
