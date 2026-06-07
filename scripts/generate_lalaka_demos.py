#!/usr/bin/env python3
"""Generate ~30-second demo MP4 for each Lalaka locale.

For each locale we produce:
  web/static/lalaka_demos/{locale}.mp4

Approach (custom mini-pipeline; does NOT call engine/pipeline.generate_fairytale):
  1. Hand-written EN script → LLM translates to all 13 in one batch
  2. One illustration (16:9, Pixar style) via existing image_generator
  3. Per locale: TTS via ElevenLabs with proper language_code + best v3 voice
  4. ffmpeg: still image + audio + 1s fade-in/fade-out = MP4

Total cost ~$0.10–0.30. Wall time ~6–10 min.

Re-runnable: skips locales that already have a demo file. Use --force to redo.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import sys
from pathlib import Path

# Allow running as a standalone script from /app inside the container
sys.path.insert(0, "/app")

import aiohttp

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "web" / "static" / "lalaka_demos"
WORK = REPO / ".lalaka_demo_work"
STATIC.mkdir(parents=True, exist_ok=True)
WORK.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("lalaka-demo")

LOCALES = ["en", "de", "es", "fr", "it", "pl", "pt-BR", "tr", "ja", "ko", "ar", "ru", "uk"]

# ElevenLabs language_code values (BCP-47 short). pt-BR uses "pt".
EL_LANG_CODE = {
    "en": "en", "de": "de", "es": "es", "fr": "fr", "it": "it", "pl": "pl",
    "pt-BR": "pt", "tr": "tr", "ja": "ja", "ko": "ko", "ar": "ar",
    "ru": "ru", "uk": "uk",
}

# Curated voice per locale, hand-picked from the ElevenLabs catalogue.
# Native (labels.language == target) preferred; multi-language warm female voices
# as fallback for locales with no good native option. Tested 2026-06-07.
CURATED_VOICES = {
    "en":    ("hpp4J3VqNfWAUOO0d1Us", "Bella - Professional, Bright, Warm"),
    "de":    ("dCnu06FiOZma2KVNUoPZ", "Mila Winter — Narration (expressive)"),
    "es":    ("EXAVITQu4vr4xnSDxMaL", "Sarah - Mature, Reassuring, Confident"),
    "fr":    ("McVZB9hVxVSk3Equu8EH", "Audrey (French native)"),
    "it":    ("wJqPPQ618aTW29mptyoc", "Ana-Rita2 (soft female)"),
    "pl":    ("xsSg7GkDPDhaGZpbKOLn", "Tomasz Z — Fairyland Storyteller"),
    "pt-BR": ("wJqPPQ618aTW29mptyoc", "Ana-Rita2 (soft female, verified pt)"),
    "tr":    ("Sm1seazb4gs7RSlUVw7c", "Anika - Animated and Friendly"),
    "ja":    ("3JDquces8E8bkmvbh6Bc", "Otani (Japanese native)"),
    "ko":    ("uyVNoMrnUku1dZyVEXwD", "Anna Kim (Korean native)"),
    "ar":    ("IES4nrmZdUBHByLBde0P", "Haytham — Conversation (Arabic native)"),
    "ru":    ("rxEz5E7hIAPk7D3bXwf6", "Anna - Calm and pleasant Russian woman"),
    "uk":    ("ymDCYd8puC7gYjxIamPt", "TatanaLuke (calm female, verified uk)"),
}

# Master script in English. ~50–60 words → ~25–30 sec spoken.
MASTER_SCRIPT_EN = (
    "Tonight, your child becomes the hero of their own fairy tale. "
    "Tell us their name, share a photo, and pick a topic. "
    "In just minutes, we craft a personal audio story — illustrated, narrated, "
    "and made just for them. A bedtime moment they'll ask for again and again."
)


async def translate_all(en_script: str) -> dict[str, str]:
    """One LLM batch call → translations for all 13 locales."""
    from engine.llm_client import _call_llm
    prompt = (
        "Translate the following Lalaka demo script into 12 languages. "
        "Keep length similar (within 20%), keep the warm/personal tone, "
        "and adapt phrasing naturally — do NOT do literal word-for-word translation.\n\n"
        f"ENGLISH SOURCE:\n{en_script}\n\n"
        "Return ONLY a JSON object with these keys (no markdown, no commentary):\n"
        "{\"de\": \"...\", \"es\": \"...\", \"fr\": \"...\", \"it\": \"...\", \"pl\": \"...\", "
        "\"pt-BR\": \"...\", \"tr\": \"...\", \"ja\": \"...\", \"ko\": \"...\", \"ar\": \"...\", "
        "\"ru\": \"...\", \"uk\": \"...\"}"
    )
    resp = await _call_llm(
        system="You are an expert children's content translator. Return only valid JSON.",
        user=prompt,
        purpose="demo_translate",
    )
    if not resp:
        raise RuntimeError("Empty translation response")
    # extract JSON
    s = resp.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1]
        if s.startswith("json"):
            s = s[4:]
        s = s.strip("` \n")
    data = json.loads(s)
    out = {"en": en_script}
    for loc in LOCALES:
        if loc == "en":
            continue
        if loc not in data:
            logger.warning("LLM missed locale %s; using EN as fallback", loc)
            out[loc] = en_script
        else:
            out[loc] = data[loc].strip()
    return out


async def pick_voice_id(locale: str) -> tuple[str, str]:
    """Return (voice_id, voice_name) for the locale.
    Uses hand-curated CURATED_VOICES if available; else falls back to intl pool scoring."""
    if locale in CURATED_VOICES:
        return CURATED_VOICES[locale]
    from engine.voice_pool_intl import get_voices_for_locale
    voices = await get_voices_for_locale(locale)
    if not voices:
        raise RuntimeError(f"No voices for {locale}")
    def score(v):
        s = 0
        if v.is_v3_verified: s += 100
        if v.gender == "female": s += 40
        if v.age_group in ("young", "middle"): s += 20
        if v.tone in ("warm", "soft"): s += 30
        return -s
    voices = sorted(voices, key=score)
    return voices[0].voice_id, voices[0].name


async def tts_one(locale: str, text: str, out_path: Path):
    """ElevenLabs synthesise; saves MP3 to out_path."""
    import os
    api_key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY not set")
    vid, vname = await pick_voice_id(locale)
    logger.info("[%s] voice: %s (%s)", locale, vname, vid)
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{vid}"
    payload = {
        "text": text,
        "model_id": "eleven_v3",
        "language_code": EL_LANG_CODE.get(locale, "en"),
        "voice_settings": {"stability": 0.45, "similarity_boost": 0.85, "style": 0.30},
    }
    headers = {"xi-api-key": api_key, "Content-Type": "application/json", "Accept": "audio/mpeg"}
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=payload, headers=headers,
                          timeout=aiohttp.ClientTimeout(total=90)) as r:
            if r.status != 200:
                body = (await r.read())[:200]
                raise RuntimeError(f"TTS failed [{r.status}]: {body!r}")
            data = await r.read()
    out_path.write_bytes(data)
    logger.info("[%s] TTS bytes: %d", locale, len(data))


async def generate_image() -> Path:
    """Create one shared illustration: cozy bedroom with starry sky, Pixar 3D style."""
    img_path = WORK / "demo_master.png"
    if img_path.exists():
        logger.info("Using cached demo image: %s", img_path)
        return img_path
    from engine.image_generator import _call_image_api
    prompt = (
        "Pixar-style 3D animated illustration: a cozy children's bedroom at bedtime. "
        "A round window shows a soft purple-pink night sky scattered with golden stars and a glowing crescent moon. "
        "On the bed sits a small plush bunny under a soft duvet. A bedside lamp casts a gentle warm glow. "
        "Toys on shelves: a tiny rocket, a stack of books, a cuddly bear. Floating fairy lights twinkle near the ceiling. "
        "Warm purple and pink palette matching the brand. No people, no text. "
        "16:9 aspect, cinematic composition, soft warm lighting, hyper-detailed but cozy."
    )
    content = [{"type": "text", "text": prompt}]
    image_bytes = await _call_image_api(
        content=content,
        scene_index=0,
        style_label="painted",
        story_id=None,
    )
    if not image_bytes:
        raise RuntimeError("Image gen returned None")
    img_path.write_bytes(image_bytes)
    logger.info("Image saved: %s (%d bytes)", img_path, img_path.stat().st_size)
    return img_path


async def make_mp4(image_path: Path, audio_path: Path, out_path: Path):
    """ffmpeg: still image + audio → mp4 with subtle ken-burns zoom."""
    # Get audio duration to set image duration
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    dur = float(stdout.decode().strip() or "30")
    # Slight slow zoom-in: 1.0 → 1.06 over duration
    fade_dur = 0.8
    fade_out_start = max(0, dur - fade_dur)
    vf = (
        f"scale=2400:-2,zoompan=z='min(zoom+0.0006,1.06)':d={int(dur*24)}:s=1920x1080:fps=24,"
        f"fade=t=in:st=0:d={fade_dur},fade=t=out:st={fade_out_start}:d={fade_dur}"
    )
    af = f"afade=t=in:st=0:d={fade_dur},afade=t=out:st={fade_out_start}:d={fade_dur}"
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(image_path),
        "-i", str(audio_path),
        "-filter_complex", f"[0:v]{vf}[v];[1:a]{af}[a]",
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "22", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        "-movflags", "+faststart",
        str(out_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {stderr.decode()[-300:]}")
    logger.info("MP4 ready: %s (%d bytes)", out_path, out_path.stat().st_size)


async def run_locale(locale: str, script: str, image_path: Path, force: bool):
    out_mp4 = STATIC / f"{locale}.mp4"
    if out_mp4.exists() and not force:
        logger.info("[%s] already exists, skipping (use --force to redo)", locale)
        return
    audio_path = WORK / f"{locale}.mp3"
    await tts_one(locale, script, audio_path)
    await make_mp4(image_path, audio_path, out_mp4)


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--locale", help="run for a single locale instead of all")
    p.add_argument("--force", action="store_true", help="overwrite existing demos")
    p.add_argument("--no-tts", action="store_true", help="dry-run: skip TTS+ffmpeg")
    args = p.parse_args()

    targets = [args.locale] if args.locale else LOCALES
    logger.info("Generating demos for: %s", targets)

    # 1. Translations
    tr_cache = WORK / "translations.json"
    if tr_cache.exists() and not args.force:
        translations = json.loads(tr_cache.read_text(encoding="utf-8"))
        logger.info("Using cached translations: %s", tr_cache)
    else:
        logger.info("Translating master script via LLM…")
        translations = await translate_all(MASTER_SCRIPT_EN)
        tr_cache.write_text(json.dumps(translations, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Saved translations: %s", tr_cache)

    for loc in targets:
        snippet = (translations[loc][:80] + "…") if len(translations[loc]) > 80 else translations[loc]
        logger.info("[%s] %s", loc, snippet)

    if args.no_tts:
        logger.info("Dry-run done (--no-tts)")
        return

    # 2. Shared illustration
    image_path = await generate_image()

    # 3. Per-locale TTS + MP4
    for loc in targets:
        try:
            await run_locale(loc, translations[loc], image_path, args.force)
        except Exception as e:
            logger.error("[%s] FAILED: %s", loc, e)

    logger.info("Done. Demos in %s", STATIC)


if __name__ == "__main__":
    asyncio.run(main())
