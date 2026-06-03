# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Skazka Bot

Telegram-бот для генерации персонализированных аудиосказок с иллюстрациями и видео.

## Команды для разработки

```bash
# Локальный стек (Postgres на хост-порту 5434, nginx для media на 8080)
docker compose up -d --build
docker logs skazka-bot -f
docker compose exec postgres psql -U skazka

# Перечитать DB-конфиг без рестарта — отправить боту команду /reload

# Тесты (запускать из корня репо, требуют .env и доступ к внешним API)
python test_e2e.py         # полный пайплайн: 3 сказки, проверка файлов/длительности
python test_callbacks.py   # порядок колбэков (audio_ready раньше illustration_ready)
```

Рантайм требует **ffmpeg** (в Dockerfile ставится автоматически; для запуска вне Docker установить отдельно).

⚠️ `.env.example` устарел — там упомянуты SQLite и Redis, но реально используется **asyncpg + Postgres** без Redis. Актуальные переменные см. в `bot/config.py` и `docker-compose.yml`.

## Главная точка входа пайплайна

`engine.pipeline.generate_fairytale(context, reference_photo_b64, on_status, on_audio_ready, on_illustration_ready)` — единственная функция оркестрации. Её вызывают и хендлеры, и оба тестовых скрипта. Колбэки срабатывают строго в порядке: `on_status` → `on_audio_ready` → `on_illustration_ready` (аудио доставляется раньше иллюстраций — это инвариант, см. `test_callbacks.py`).

## Архитектура

```
Telegram (aiogram3) → LLM текст сказки → LLM screenplay JSON → Voice assignment
                        → ElevenLabs TTS → ffmpeg concat + ambient
                        → LLM scene split (с таймкодами) → Gemini Pro Image (иллюстрации)
                        → ffmpeg video (24fps, crop-to-fill)
                        → Доставка: MP4 видео (или MP3 + ссылка если >50MB)
```

## Структура файлов

```
bot/
  __main__.py              — Entry point, DB/config init, router registration
  config.py                — Pydantic settings from .env
  notify.py                — Admin notifications (errors, new users, completed stories)
  states/create.py         — FSM state definitions
  keyboards/inline.py      — Inline keyboard builders
  filters/                 — aiogram filters (доступ, admin-only и т.п.)
  middlewares/             — aiogram middlewares (rate limit, guard, логирование)
  handlers/
    utils.py               — Shared helpers (_guard, _msg, _get_text, _show_story, etc.)
    create.py              — Entry points: /new, on_create, on_input
    compose.py             — Story composition: compose, edit, regenerate
    generate.py            — Generation pipeline: photo, TTS, illustrations, video, feedback
    start.py               — /start, /cancel, /reload commands

engine/
  pipeline.py              — Main orchestration: TTS → timeline → scene split → illustrations → video
  llm_client.py            — generate_story_text(), convert_to_screenplay(), generate_screenplay()
  tts_client.py            — ElevenLabs TTS batch synthesis
  image_generator.py       — Scene split + Pixar illustration generation
  audio_mixer.py           — FFmpeg: concat, ambient mix, video creation
  voice_pool.py            — 58 voices + scoring algorithm
  story_parser.py          — Audio tags, ambient mapping
  transcribe.py            — Voice message transcription
  http_session.py          — Shared aiohttp session

db/
  database.py              — PostgreSQL schema + async CRUD (8 tables)
  config_manager.py        — Dynamic config with 30s TTL cache (79 keys)

assets/ambient_sounds/     — 16 ambient MP3 files
```

## Двухшаговая генерация

1. **Шаг 1** — LLM пишет plain text сказки (быстро) → показываем пользователю → правки
2. **Шаг 2** — "Озвучить" → конвертация в screenplay JSON → TTS → illustrations → video

## Pipeline (post-TTS scene split)

1. Convert text → screenplay JSON (characters, segments, emotions, audio tags)
2. Voice assignment (58 voices, scoring по gender/age/role/tone)
3. TTS generation (ElevenLabs v3, 10 concurrent)
4. Concat segments + pauses (0.7с/1.3с) + ambient mix (16 sounds)
5. Build timeline с реальными таймкодами (cumulative seconds)
6. Scene split (LLM видит таймкоды → точная привязка иллюстраций к аудио)
7. Illustrations (Gemini 3 Pro Image, photo-first подход, character bible, полный текст сцены)
8. Video (24fps, keyframes every 2s, crop-to-fill 1920×1080)

## API и модели (.env + DB config)

| Сервис | Модель (из DB config) | Для чего |
|--------|----------------------|----------|
| OpenRouter | `model.llm` (Gemini 2.5 Pro) | Текст сказки + screenplay convert. Fallback: `model.llm_fallback` (Gemini 3.5 Flash + `reasoning.effort=high` через `model.llm_fallback_reasoning`). Таймаут `llm.call_timeout_sec=90s`. Flash vanilla без reasoning даёт ~40% empty content — high pinned даёт 5/5 reliability (тест 2026-06-03) |
| OpenRouter | `model.scene_split` (Gemini 3.5 Flash) | Scene split (структурный JSON). ⚠ Pro здесь даёт пустой content — съедает бюджет на reasoning |
| OpenRouter | `model.image` (Gemini 3.1 Flash Image preview) | Иллюстрации (Pixar-стиль). Premium-альтернатива через swap config — Gemini 3 Pro Image |
| OpenRouter | `model.transcribe` (Gemini 2.5 Flash) | Транскрипция голосовых |
| ElevenLabs | `model.tts` (eleven_v3) | TTS, 58 голосов, Pro план |

Все модели и параметры меняются через DB config без деплоя.

## Пользовательский флоу

1. `/start` или `/new` → инструкция
2. Текст → сразу генерация сказки (без подтверждения)
3. Голос → транскрипция → "Вот что я услышал" → подтверждение
4. Текст сказки → кнопка "Озвучить" (правки — просто отправить текст/голос)
5. Запрос фото ребёнка (одно) или "Озвучить без фото"
6. Генерация: стикер + статус → TTS → illustrations → video
7. MP4 видео → "Как вам сказка?" → фидбек
8. `/cancel` — отмена на любом этапе

## База данных (PostgreSQL)

8 таблиц:
- `users` — Telegram пользователи
- `stories` — сказки (title, context, duration, cost, status)
- `story_revisions` — правки (edit/regenerate с текстом)
- `voice_assignments` — назначения голосов
- `api_calls` — все API вызовы (request/response text, duration, cost_usd)
- `media_files` — медиафайлы с public URLs (audio, video, illustrations)
- `errors` — ошибки с traceback
- `config` — 79 динамических ключей (промпты, модели, параметры, сообщения)

## Динамический конфиг (config table)

79 ключей в категориях: prompt, model, llm, tts, audio, video, voice, ui, msg, pricing.
TTL кэша: 30 секунд. `/reload` — мгновенное обновление.
Все системные сообщения бота — в категории `msg.*` (30 ключей).

## Admin функции

- `/reload` — перечитать конфиг из DB
- Уведомления в Telegram: 👋 новый юзер, ✅ готовая сказка (с ссылками), 🚨 ошибки (с traceback)
- Rate limit: 5 сказок/час/юзер
- Медиафайлы по HTTP: `http://95.216.117.49/media/{order_id}/`

## Валидация (v1.1)

- Текст: max 2000 символов
- Голос: max 60 секунд
- Фото: max 10MB, только JPEG/PNG
- LLM output: title 200, story 15000, segments max 60
- Длинные сегменты разбиваются по предложениям (не обрезаются)
- HTML escaping для всего текста из LLM
- Таймауты: TTS 5мин, illustrations 10мин
- Double-click protection (_guard)
- /start блокируется во время генерации

## Стоимость (из DB api_calls)

- LLM (Gemini 2.5 Pro): ~$0.002/сказка (story_text) + ~$0.004 (screenplay) + scene_split
- TTS (ElevenLabs): ~$0.10/сказка
- Иллюстрации (Gemini 3.1 Flash Image × 4): ~$0.12/сказка
- **Итого: ~$0.22-0.25/сказка**

## Команды деплоя

```bash
ssh root@95.216.117.49
cd /opt/skazka-bot && git pull && docker compose up -d --build
docker logs skazka-bot -f
docker compose exec postgres psql -U skazka  # DB доступ
```

## Что нельзя ломать

- Двухшаговая генерация: текст отдельно от озвучки
- Post-TTS scene split с таймкодами — обеспечивает sync видео
- Continuous scene ranges (без gaps)
- Паузы: 0.7с (один голос), 1.3с (смена голоса)
- 5с ambient tail в конце
- Graceful degradation: картинки упали → MP3 без видео
- Video duration = audio duration
- Photo-first промпт для face preservation

## Yandex Direct API v5 — гочи (запоминать)

Из опыта создания кампании «СКАЗИК — WARM Поиск» 2026-06-01:

- **PriorityGoals.Items[]** при `campaigns/update` требует поле `Operation: "SET"` в каждом item, иначе 8000 «отсутствует Operation»
- **AutotargetingCategories** (на keyword id, через `keywords/update`): массив `[{Category, Value}]` НАПРЯМУЮ, без обёртки `Items`; значения `YES`/`NO` в `Value` (не `IsEnabled`)
- **Запрещено выключать все 5 категорий** автотаргетинга — хотя бы одна должна быть YES. У нас в HOT и WARM: только `EXACT` ON
- **CounterIds** для связки Метрики: поле `TextCampaign.CounterIds.Items` (массив counter_id)
- **AttributionModel**: обычно `"AUTO"`
- Новые объявления создаются в **DRAFT/OFF**, нужен явный `ads/moderate` чтобы запустить модерацию
- Кампания может быть `State=ON, Status=MODERATION` — показов нет пока хотя бы одно объявление не пройдёт модерацию (~1-24ч)
- `DailyBudget` несовместим с `AVERAGE_CPC` (нужна ручная стратегия)
- `ENABLE_EXTENDED_AD_TITLE` — больше не поддерживается, warning 10163
- `bids/get` для несуществующего keyword может возвращать данные — НЕ показатель что фраза реально ищется. Volume проверять через **Bukvarix free API** (`api_key=free`, лимит ~8 запросов/минуту, generic фразы троттлятся жёстко)
- Все денежные значения в micro-units: `4₽ = 4000000`, `7000₽ = 7000000000`, `499₽ = 499000000`

### Активные кампании skazik.app

- **710328773** СКАЗИК — Поиск (HOT) — узкая ниша «персональная сказка». ~95 exact/мес потолок.
- **710358360** СКАЗИК — WARM Поиск — generic bedtime «сказка на ночь», cap 7k₽/нед, landing `/night`
- **710328840** СКАЗИК — РСЯ — SUSPENDED (мусорный трафик)

⚠ **НЕ ТРОГАТЬ** кампании Левина: БРО.ХИТ!, СКРШ.*, GetVideo.*, ЭЙБРО.*, FEDERAL TV — это его другие бизнесы в общем аккаунте.

## Бэклог

- Поддержка до 3 детей с отдельными фото
- Face swap с выбором конкретного лица (Segmind FaceSwap V3)
- Модель с нативным face preservation (IP-Adapter, InstantID)
- Character passport (master reference + turnarounds)
- Перегенерация озвучки без пересоздания сценария
- Параллельная TTS + сбор фото
