"""Generate a ~1 minute multi-voice audio preview from a plain story text.

Used by both Skazik (/order/{oid}/demo) and Lalaka (lalaka /order/{oid}/demo)
to pitch the full multi-voice acting experience BEFORE the user pays.

Pipeline:
  1. Slice the first ~145 words of the story (≈60s of narration).
  2. Convert the slice into a screenplay JSON via llm_client.convert_to_screenplay.
     The converter parses characters + emotion + segments.
  3. Assign each character a voice via voice_pool.pick_voice.
  4. Synthesize all segments in parallel via tts_client.synthesize_batch.
  5. concat_segments with same-speaker / speaker-change pause logic.
  6. mix_with_ambient adds a quiet fireplace bed under the speech.

If the screenplay step fails (it's known-flaky), we fall back to single-voice
narration so the demo still plays — just with one voice instead of acting.

Cost target: ~$0.025 / generation
  ~$0.001 LLM screenplay convert
  ~$0.024 ElevenLabs TTS for 145 words (single chunk; concat is free)
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


async def _apply_fadeout(path: Path, fade_seconds: float = 5.0) -> None:
    """Apply a smooth 5s fade-out at the end of an MP3, in place."""
    from engine import audio_mixer
    try:
        dur = await audio_mixer.get_duration(path)
    except Exception:
        return
    if dur <= fade_seconds + 0.5:
        return
    fade_start = max(0.0, dur - fade_seconds)
    tmp = path.with_suffix(".faded.mp3")
    # Linear (triangular) amplitude fade — explicit `tri` is the default but
    # making it explicit keeps the intent clear next to the previous `exp`
    # experiment which was perceived as too abrupt.
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-i", str(path),
        "-af", f"afade=t=out:st={fade_start}:d={fade_seconds}:curve=tri",
        "-c:a", "libmp3lame", "-b:a", "128k",
        str(tmp),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode == 0 and tmp.exists() and tmp.stat().st_size > 1024:
        tmp.replace(path)
    else:
        logger.warning("demo fadeout ffmpeg failed: %s", stderr.decode()[-200:] if stderr else "")
        tmp.unlink(missing_ok=True)


def text_slice(story_text: str, target_words: int = 100) -> str:
    """First ~target_words of the story, stopping on the nearest sentence
    boundary so the preview never cuts mid-word. Tuned for ~45-60 sec total
    audio. Standard verbatim screenplay convert means no slow-down padding,
    so 150 words of source ≈ 50s spoken at normal pace. Multi-voice happens
    naturally only when the source has dialogue in this window — that's the
    honest signal."""
    text = (story_text or "").strip()
    if not text:
        return ""
    words = text.split()
    if len(words) <= target_words:
        return text
    head = " ".join(words[:target_words])
    for i in range(len(head) - 1, max(0, len(head) - 200), -1):
        if head[i] in ".!?":
            return head[: i + 1]
    return head


_DEMO_SCREENPLAY_PROMPT_RU = """Преобразуй короткий отрывок сказки в screenplay JSON для МНОГОГОЛОСОЙ озвучки.

Это первая ~минута длинной сказки — нужно показать, что озвучка многоголосая. \
ОБЯЗАТЕЛЬНО выдели как минимум двух персонажей:

1. "narrator" — Рассказчик (female/middle/narrator/тёплая, спокойная). Ему достаются \
описания сцен, действий, переходов: «жил-был», «однажды», «вдруг», «в этот момент».

2. ПРОТАГОНИСТ — главный герой (имя берётся из текста; обычно первый ребёнок). \
Создай для него отдельный character с id = его имя в нижнем регистре латиницей \
(например "lily", "sofiyka", "petya"). Этому персонажу отдай ВСЕ предложения, \
которые передают ЕГО действия, мысли, эмоции, удивление, восторг, страх, надежду, \
описывают ЕГО ощущения. Даже если в тексте нет прямой речи — голос ребёнка вживается \
в любые «маленькому стало интересно», «она почувствовала», «он подумал».

3. Если в отрывке упоминается второй персонаж (мама, папа, друг, питомец) — \
добавь и его как character с детским/животным голосом.

Распредели сегменты так, чтобы РАССКАЗЧИК и ПРОТАГОНИСТ чередовались, давая \
ощущение живого исполнения. Целься в 6-10 сегментов общей длины ~700-1100 символов.

Текст:
{text}

Верни ТОЛЬКО валидный JSON без markdown:
{{
  "title": "{title}",
  "characters": [
    {{"id":"narrator","name":"Рассказчик","gender":"female","age":"middle","role":"narrator","personality":"тёплая и спокойная"}},
    {{"id":"<имя>","name":"<имя на родном языке>","gender":"<female|male>","age":"child","role":"hero","personality":"любознательная и добрая"}}
  ],
  "segments": [
    {{"character_id":"narrator","emotion":"cheerful","pace":"normal","text":"Текст рассказчика."}},
    {{"character_id":"<имя>","emotion":"cheerful","pace":"normal","text":"Текст с точки зрения героя."}}
  ],
  "scenes": [{{"segments":[0,1],"ambient":"forest"}}]
}}

Правила:
- character_id у каждого сегмента ОБЯЗАН существовать в characters
- Сегмент до 250 символов; новый сегмент = смена говорящего
- НЕ используй [slows down] и [pause] — для демо нужен компактный темп
- Допустимы лёгкие эмо-тэги [happy], [whispers] в начале сегмента — но не обязательно
- emotion: neutral|cheerful|excited|nervous|sad|whisper|soft|mysterious
- pace: normal (НЕ slow — демо должно быть быстрым)
- ambient: forest|night|fire|stream|magic|garden|meadow|wind
- characters должно содержать МИНИМУМ 2 записи (narrator + протагонист)"""


_DEMO_SCREENPLAY_PROMPT_EN = """Convert this short fairy-tale excerpt into a MULTI-VOICE screenplay JSON.

This is the first ~minute of a longer story — the demo MUST showcase that our \
narration is multi-voiced. ALWAYS extract at least TWO characters:

1. "narrator" — female, middle-aged, warm. Gets pure scene-setting sentences \
("Once upon a time", "Suddenly", "Far away in the forest").

2. THE PROTAGONIST — the child the story is about. Read the text and pick the \
hero's name (first proper noun, or from title). Create a character with id = \
hero's name in lowercase (e.g. "lily", "leo"). Assign to this character ALL \
sentences that describe their actions, feelings, thoughts, observations, \
wonder, fear, hope — EVEN if there is no quoted dialogue. The child voice \
inhabits "she felt", "he wondered", "the little one was curious".

3. If a second character (mom, dad, friend, pet) is mentioned, add them too \
with a fitting voice (child / animal / female / male).

Interleave NARRATOR and PROTAGONIST segments so the listener hears voice \
changes within the first 30 seconds. Aim for 6-10 segments, ~700-1100 chars.

Text:
{text}

Return ONLY valid JSON, no markdown:
{{
  "title": "{title}",
  "characters": [
    {{"id":"narrator","name":"Narrator","gender":"female","age":"middle","role":"narrator","personality":"warm and calm"}},
    {{"id":"<name>","name":"<HeroName>","gender":"<female|male>","age":"child","role":"hero","personality":"curious and kind"}}
  ],
  "segments": [
    {{"character_id":"narrator","emotion":"cheerful","pace":"normal","text":"Narrator text."}},
    {{"character_id":"<name>","emotion":"cheerful","pace":"normal","text":"Hero-perspective text."}}
  ],
  "scenes": [{{"segments":[0,1],"ambient":"forest"}}]
}}

Rules:
- every segment.character_id MUST exist in characters
- segments up to 250 chars; new segment when speaker changes
- DO NOT use [slows down] or [pause] tags — the demo needs a compact pace
- Light emotional tags [happy], [whispers] are OK at the start, but optional
- emotion: neutral|cheerful|excited|nervous|sad|whisper|soft|mysterious
- pace: normal (NOT slow — demo must be brisk)
- ambient: forest|night|fire|stream|magic|garden|meadow|wind
- characters MUST contain at least 2 entries (narrator + protagonist)"""


async def _demo_convert_screenplay(title: str, text: str, locale: str) -> dict | None:
    """Custom multi-voice screenplay for demo path. Falls back to standard
    convert_to_screenplay if the demo-specific call fails."""
    import json as _json
    from engine.llm_client import _call_llm, _LOCALE_LANG_NAME
    from db.config_manager import cfg
    system = await cfg.get("prompt.screenplay_convert_system",
                            "Ты генерируешь ТОЛЬКО валидный JSON.")
    if locale == "ru" or not locale:
        prompt_template = _DEMO_SCREENPLAY_PROMPT_RU
    else:
        prompt_template = _DEMO_SCREENPLAY_PROMPT_EN
    prompt = prompt_template.format(title=title, text=text[:3000])

    for attempt in range(1, 3):
        response = await _call_llm(system=system, user=prompt, purpose="demo_screenplay_convert")
        if not response or not response.strip():
            continue
        raw = response.strip()
        if raw.startswith("```"):
            raw = raw.split("```", 2)[1] if raw.count("```") >= 2 else raw
            if raw.lower().startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        try:
            data = _json.loads(raw)
        except Exception as e:
            logger.warning("demo screenplay JSON parse failed (attempt %d): %s", attempt, e)
            continue
        if not data.get("characters") or not data.get("segments"):
            continue
        # Sanity: ensure every segment.character_id exists in characters
        char_ids = {c["id"] for c in data["characters"]}
        data["segments"] = [s for s in data["segments"] if s.get("character_id") in char_ids]
        if not data["segments"]:
            continue
        return data

    # Fallback to standard converter if custom prompt failed
    try:
        from engine.llm_client import convert_to_screenplay as _std
        return await _std(title, text, locale=locale)
    except Exception:
        return None


_LOCALE_TO_TTS_LANG = {
    "en": "en", "de": "de", "es": "es", "fr": "fr", "it": "it",
    "pl": "pl", "pt-BR": "pt", "tr": "tr", "ja": "ja", "ko": "ko",
    "ar": "ar", "ru": "ru",
}


# Map screenplay's `ambient` field to a real file under assets/ambient_sounds/.
# Fireplace was the previous default and listeners described its crackle as
# "strange" under speech, so we now prefer night_forest (quiet crickets/owls)
# as the calming fallback.
_AMBIENT_FILE_MAP = {
    "forest":   "forest_ambience.mp3",
    "night":    "night_forest.mp3",
    "fire":     "fireplace.mp3",
    "stream":   "stream_water.mp3",
    "magic":    "magic_sparkle.mp3",
    "garden":   "garden_insects.mp3",
    "meadow":   "birds_meadow.mp3",
    "wind":     "wind_blowing.mp3",
    "ocean":    "ocean_waves.mp3",
    "sea":      "ocean_waves.mp3",
    "rain":     "rain_storm.mp3",
    "snow":     "snow_wind.mp3",
    "city":     "city_market.mp3",
    "castle":   "castle_echo.mp3",
    "cave":     "cave_ambience.mp3",
    "space":    "space_ambient.mp3",
    "sky":      "wind_blowing.mp3",
    "storm":    "thunderstorm.mp3",
    "birds":    "birds_meadow.mp3",
}


def _ambient_from_screenplay(screenplay: dict, ambient_dir: Path) -> Path | None:
    """Pick an ambient file based on the screenplay's first scene type.
    Falls back to night_forest (quiet, bedtime-fitting). Returns None if no
    file is available at all."""
    try:
        first_scene = (screenplay.get("scenes") or [{}])[0]
        amb_key = (first_scene.get("ambient") or "").strip().lower()
    except Exception:
        amb_key = ""
    name = _AMBIENT_FILE_MAP.get(amb_key, "night_forest.mp3")
    p = ambient_dir / name
    if p.exists():
        return p
    fallback = ambient_dir / "night_forest.mp3"
    return fallback if fallback.exists() else None


async def _single_voice_demo(text: str, out_path: Path, fallback_voice_id: str,
                              ambient_path: Path | None, language_code: str) -> None:
    """Plan B when screenplay convert flakes — one narrator, one MP3."""
    from db.config_manager import cfg
    from engine import tts_client, audio_mixer
    seg = {
        "text": text,
        "voice_id": fallback_voice_id,
        "stability": float(await cfg.get("tts.default_stability", 0.45)),
        "similarity": float(await cfg.get("tts.default_similarity", 0.80)),
        "style": float(await cfg.get("tts.default_style", 0.25)),
        "language_code": language_code,
    }
    results = await tts_client.synthesize_batch([seg], max_concurrent=1)
    if not results or not results[0]:
        raise RuntimeError("TTS empty")
    raw_path = out_path.with_suffix(".raw.mp3")
    raw_path.write_bytes(results[0])
    try:
        if ambient_path and ambient_path.exists():
            await audio_mixer.mix_with_ambient(
                speech_path=raw_path, ambient_path=ambient_path,
                output_path=out_path, ambient_vol=0.12, tail_seconds=0.0,
            )
        else:
            raw_path.rename(out_path)
    finally:
        if raw_path.exists() and out_path.exists():
            raw_path.unlink(missing_ok=True)
    await _apply_fadeout(out_path, fade_seconds=5.0)


async def build_demo(story_text: str, title: str, out_path: Path,
                     locale: str = "ru",
                     fallback_voice_id: str = "ymDCYd8puC7gYjxIamPt",
                     ambient_path: Path | None = None) -> dict:
    """Generate a multi-voice ~60s demo audio file at out_path.

    Returns a small dict with metadata about what was generated:
      {ok, mode: 'multi'|'single', num_voices, num_segments}
    Raises on hard failure (no audio at all).
    """
    text = text_slice(story_text)
    if not text:
        raise ValueError("empty story text")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    work_dir = out_path.parent / f"_work_{out_path.stem}"
    work_dir.mkdir(parents=True, exist_ok=True)

    lang_code = _LOCALE_TO_TTS_LANG.get(locale, "en")

    # ── Step 1: screenplay convert (verbatim) ──
    # We previously used a demo-only prompt that forced the LLM to extract a
    # second "protagonist" character even when no dialogue existed. That gave
    # multi-voice previews more often, but the LLM had to rephrase / invent
    # first-person lines to make the alternation work — so the demo audio
    # diverged from the real story text. Reverted to the standard converter
    # which preserves text and only splits on actual speaker changes. The
    # demo is multi-voice IFF the source text already has dialogue early on,
    # which is the honest signal: what you hear is what you get.
    screenplay = None
    try:
        from engine.llm_client import convert_to_screenplay
        screenplay = await convert_to_screenplay(title or "Preview", text, locale=locale)
        if not screenplay or not screenplay.get("segments") or not screenplay.get("characters"):
            screenplay = None
    except Exception as e:
        logger.warning("demo screenplay failed, will fallback to single voice: %s", e)
        screenplay = None

    if not screenplay:
        await _single_voice_demo(text, out_path, fallback_voice_id, ambient_path, lang_code)
        return {"ok": True, "mode": "single", "num_voices": 1, "num_segments": 1}

    # ── Step 2: pick voices per character ──
    from engine.voice_pool import pick_voice
    voice_map = {}
    assigned: dict[str, str] = {}
    for char in screenplay.get("characters", []):
        try:
            v = await pick_voice(
                gender=char.get("gender", "female"),
                age=char.get("age", "middle"),
                role=char.get("role", "narrator"),
                already_used=assigned,
            )
        except Exception as e:
            logger.warning("pick_voice failed for char %s: %s", char.get("id"), e)
            continue
        voice_map[char["id"]] = v
        assigned[char["id"]] = v.voice_id

    if not voice_map:
        await _single_voice_demo(text, out_path, fallback_voice_id, ambient_path, lang_code)
        return {"ok": True, "mode": "single", "num_voices": 1, "num_segments": 1}

    # ── Step 3: build TTS requests ──
    from engine.story_parser import build_tagged_text
    from db.config_manager import cfg
    def_stab = float(await cfg.get("tts.default_stability", 0.45))
    def_sim = float(await cfg.get("tts.default_similarity", 0.80))
    def_style = float(await cfg.get("tts.default_style", 0.25))

    tts_requests = []
    seg_char_ids = []

    # Prepend the randomized brand intro so the demo matches the real story.
    # IMPORTANT: use the order's actual title (passed via `title` arg) — the
    # screenplay's `title` field is the LLM's own naming of the short slice,
    # which often diverges from the buyer-facing title shown on /order.
    from engine.story_intro import pick_intro_text
    intro_text = pick_intro_text(title or screenplay.get("title", ""),
                                  locale=(locale or "ru"))
    if intro_text:
        narrator_voice = voice_map.get("narrator") or next(iter(voice_map.values()))
        tts_requests.append({
            "text": intro_text,
            "voice_id": narrator_voice.voice_id,
            "stability": getattr(narrator_voice, "default_stability", def_stab),
            "similarity": getattr(narrator_voice, "default_similarity", def_sim),
            "style": getattr(narrator_voice, "default_style", def_style),
            "language_code": lang_code,
        })
        seg_char_ids.append("narrator")
        logger.info("demo intro prepended (title=%r): %r",
                     title, intro_text)

    segments = screenplay.get("segments", [])
    for seg in segments:
        char_id = seg.get("character_id") or "narrator"
        voice = voice_map.get(char_id) or voice_map.get("narrator") or next(iter(voice_map.values()))
        tagged = build_tagged_text(
            seg.get("text", ""),
            seg.get("emotion", "neutral"),
            seg.get("pace", "normal"),
            is_narrator=(char_id == "narrator"),
        )
        tts_requests.append({
            "text": tagged,
            "voice_id": voice.voice_id,
            "stability": getattr(voice, "default_stability", def_stab),
            "similarity": getattr(voice, "default_similarity", def_sim),
            "style": getattr(voice, "default_style", def_style),
            "language_code": lang_code,
        })
        seg_char_ids.append(char_id)

    if not tts_requests:
        await _single_voice_demo(text, out_path, fallback_voice_id, ambient_path, lang_code)
        return {"ok": True, "mode": "single", "num_voices": 1, "num_segments": 1}

    # ── Step 4: synthesize all segments ──
    from engine import tts_client, audio_mixer
    audio_chunks = await tts_client.synthesize_batch(tts_requests, max_concurrent=3)

    # ── Step 5: write each chunk, concat with pauses ──
    seg_files = []
    kept_char_ids = []
    for i, audio in enumerate(audio_chunks):
        if audio is None:
            continue
        sf = work_dir / f"seg_{i:02d}.mp3"
        sf.write_bytes(audio)
        seg_files.append(sf)
        kept_char_ids.append(seg_char_ids[i])

    if not seg_files:
        await _single_voice_demo(text, out_path, fallback_voice_id, ambient_path, lang_code)
        return {"ok": True, "mode": "single", "num_voices": 1, "num_segments": 1}

    dry_path = work_dir / "dry.mp3"
    await audio_mixer.concat_segments(seg_files, dry_path, character_ids=kept_char_ids)

    # ── Step 6: mix ambient ──
    # Prefer ambient hinted by the screenplay's first scene; if the caller
    # passed a hard ambient_path it always wins.
    chosen_ambient = ambient_path
    if not chosen_ambient and screenplay:
        chosen_ambient = _ambient_from_screenplay(
            screenplay, Path("/app/assets/ambient_sounds"))
    try:
        if chosen_ambient and chosen_ambient.exists():
            # tail_seconds=0 — no ambient-only "tail" after speech ends,
            # so the file ends precisely with the speech and the final fade-out
            # works on the actual voice instead of trailing ambient.
            await audio_mixer.mix_with_ambient(
                speech_path=dry_path, ambient_path=chosen_ambient,
                output_path=out_path, ambient_vol=0.10, tail_seconds=0.0,
            )
        else:
            dry_path.rename(out_path)
    except Exception as e:
        logger.warning("demo ambient mix failed, serving dry: %s", e)
        try:
            dry_path.rename(out_path)
        except Exception:
            pass

    # ── Cleanup work files ──
    try:
        for f in work_dir.glob("*"):
            f.unlink(missing_ok=True)
        work_dir.rmdir()
    except Exception:
        pass

    if not out_path.exists() or out_path.stat().st_size < 1024:
        raise RuntimeError("demo build produced no output")

    await _apply_fadeout(out_path, fade_seconds=5.0)

    return {
        "ok": True,
        "mode": "multi",
        "num_voices": len(voice_map),
        "num_segments": len(seg_files),
    }
