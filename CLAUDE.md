# Skazka Bot

Telegram-бот для генерации персонализированных аудиосказок с иллюстрациями.

## Архитектура

```
Telegram (aiogram3) → LLM сценарий → Voice assignment → ElevenLabs TTS → ffmpeg mix
                                                        → Gemini Flash Image (иллюстрации)
                                                        ↓
                                          Доставка: MP3 → картинки по одной → MP4
```

Ключевые модули:
- `bot/` — Telegram handlers, FSM, keyboards
- `bot/handlers/create.py` — основной флоу создания сказки, callback-доставка
- `engine/pipeline.py` — оркестрация: TTS + картинки параллельно, callbacks
- `engine/image_generator.py` — Pixar-стиль иллюстрации через Gemini 2.5 Flash Image
- `engine/voice_pool.py` — 20 голосов ElevenLabs, scoring по gender/age/role/tone
- `engine/tts_client.py` — ElevenLabs TTS с батчингом
- `engine/audio_mixer.py` — ffmpeg: concat сегментов, амбиент, MP4 видео
- `engine/llm_client.py` — генерация сценария через Gemini 2.5 Pro
- `engine/story_parser.py` — SSML-теги для TTS, маппинг амбиентов
- `engine/transcribe.py` — голосовой ввод через Gemini Flash
- `assets/ambient_sounds/` — 4 фоновых звука (forest, cave, stream, night)

## API и ключи (.env)

| Сервис | Переменная | Модель | Для чего |
|--------|-----------|--------|----------|
| Telegram | BOT_TOKEN | — | @SkazikBot |
| OpenRouter | OPENROUTER_API_KEY | `google/gemini-2.5-pro-preview-03-25` | Сценарий + разбивка на сцены |
| OpenRouter | (тот же ключ) | `google/gemini-2.5-flash-image` | Иллюстрации (Pixar-стиль) |
| ElevenLabs | ELEVENLABS_API_KEY | v3 API | TTS, 20-голосовой пул. План Pro, 500K символов/мес |
| ElevenLabs | ELEVENLABS_PROXY | — | SOCKS5 прокси (гео-блок из РФ) |
| Groq | GROQ_API_KEY | whisper | Транскрипция голосовых (резерв) |

## Ограничения

- ElevenLabs v3: max 3000 символов/запрос, но мы шлём по ~130 символов (1 сегмент = 1 голос)
- Gemini 2.5 Flash Image: ~10-15 сек на картинку, $0.30/M input + $2.50/M output
- MAX_CONCURRENT_TTS=10 (Pro план позволяет)
- Сказка = ~20 сегментов, ~2700 символов, ~3 мин аудио, 7-8 иллюстраций
- Общее время генерации: ~3-5 мин (TTS ~30с, картинки ~100с, видео ~40с)

## Пользовательский флоу

1. `/start` → «Создать сказку»
2. Голос/текст — всё о ребёнке одним сообщением
3. Подтверждение ввода → «Сочинить сказку»
4. Текст сказки → «Озвучить» / «Изменить» / «Заново»
5. Запрос фото ребёнка (или пропуск)
6. Генерация: TTS + картинки параллельно
7. Доставка потоковая:
   - MP3 отправляется сразу после сведения (callback `on_audio_ready`)
   - Картинки приходят по одной по мере генерации (callback `on_illustration_ready`)
   - `asyncio.Event` гарантирует: картинки только ПОСЛЕ MP3
   - MP4 видео в конце
8. Фидбек → «Создать ещё»

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
- Graceful degradation: если картинки упали — отдаём MP3 без них
- Видео: Ken Burns анимация (плавный zoom/pan чередуется по сценам) вместо статичных слайдов

## Voice Pool

20 голосов в `engine/voice_pool.py` (10 женских, 10 мужских).
Автоматический подбор через scoring: gender × age × role × tone.

Scoring-правила:
- Для `age: "child"` — штраф 80% за `deep`/`authoritative` голоса, бонус 30% за `bright`/`soft`
- Роль `animal` предпочитает `warm` и `bright` тона
- Уже использованные голоса получают штраф 50% (разнообразие)

Все голоса протестированы на русском.

## Команды

```bash
# Локально
cd skazka_bot && python -m bot

# Деплой (сервер 95.216.117.49, Docker)
ssh root@95.216.117.49
cd /opt/skazka-bot && git pull && docker compose up -d --build
docker logs skazka-bot --tail 50
docker logs skazka-bot -f  # realtime

# E2E тест (внутри Docker)
docker compose exec skazka-bot python test_callbacks.py

# Полный тест (3 сказки)
docker compose exec skazka-bot python test_e2e.py
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

- Нет детских голосов в пуле ElevenLabs (компенсируем scoring-штрафами)
- Генеративные модели иногда дублируют/искажают персонажей на иллюстрациях
- Нет кэширования сценариев / голосов между сессиями
- Нет системы оплаты / лимитов на пользователя
