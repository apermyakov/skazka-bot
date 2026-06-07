#!/usr/bin/env python3
"""Generate real-story-slice demos for each Lalaka locale.

Per locale we produce a ~25s MP4 that shows what the actual product makes:
  - 3 Pixar-style illustrations of a bedtime scene with a child
  - Native-voice narration of an 80-word fairy-tale excerpt
  - ffmpeg slideshow with crossfade timed to the audio

The child is shown from behind / face hidden so the viewer projects their own
child onto the hero (a Lalaka brand storytelling trick — when you upload a
photo we make the face look like your child, so demo keeps the face neutral).

Output: web/static/lalaka_demos/{locale}.mp4 (replaces marketing-clip demos)
Cost: ~$1.50 total (3 illustrations × 13 + TTS).
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/app")

import aiohttp

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "web" / "static" / "lalaka_demos"
WORK = REPO / ".lalaka_story_work"
STATIC.mkdir(parents=True, exist_ok=True)
WORK.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("story-demo")

LOCALES = ["en", "de", "es", "fr", "it", "pl", "pt-BR", "tr", "ja", "ko", "ar", "ru", "uk"]

EL_LANG_CODE = {
    "en":"en","de":"de","es":"es","fr":"fr","it":"it","pl":"pl","pt-BR":"pt",
    "tr":"tr","ja":"ja","ko":"ko","ar":"ar","ru":"ru","uk":"uk",
}

# Hand-curated narrator per locale (from CURATED_NARRATORS in engine/voice_pool_intl)
CURATED_VOICES = {
    "en":    ("hpp4J3VqNfWAUOO0d1Us", "Bella"),
    "de":    ("dCnu06FiOZma2KVNUoPZ", "Mila Winter"),
    "es":    ("EXAVITQu4vr4xnSDxMaL", "Sarah"),
    "fr":    ("McVZB9hVxVSk3Equu8EH", "Audrey"),
    "it":    ("wJqPPQ618aTW29mptyoc", "Ana-Rita2"),
    "pl":    ("xsSg7GkDPDhaGZpbKOLn", "Tomasz Z"),
    "pt-BR": ("wJqPPQ618aTW29mptyoc", "Ana-Rita2"),
    "tr":    ("Sm1seazb4gs7RSlUVw7c", "Anika"),
    "ja":    ("3JDquces8E8bkmvbh6Bc", "Otani"),
    "ko":    ("uyVNoMrnUku1dZyVEXwD", "Anna Kim"),
    "ar":    ("IES4nrmZdUBHByLBde0P", "Haytham"),
    "ru":    ("rxEz5E7hIAPk7D3bXwf6", "Anna"),
    "uk":    ("ymDCYd8puC7gYjxIamPt", "TatanaLuke"),
}

# Master story scene in EN — ~80 words, bedtime, magical-helper theme.
# Designed for 3 illustrations: (1) child tucked in, (2) star arrives, (3) child sleeping.
MASTER_STORY = (
    "Lily was tucked into her cosy bed when the moon peeked through the curtains. "
    "Suddenly, a tiny silver star slipped through and floated down to her pillow. "
    "\"Don't be afraid,\" it whispered with a soft golden glow. "
    "\"I'll stay right here until you fall asleep.\" "
    "Lily smiled, her eyes growing heavy. The little star began to hum a gentle song, "
    "and by the third tiny note, she was already drifting into the warmest dream."
)

# Three scenes for illustrations — same Pixar style as real product
SCENE_PROMPTS = [
    ("scene1_bedtime",
     "Pixar-style 3D animated illustration: a cosy children's bedroom at bedtime. "
     "A small child (back view, only the top of the head and shoulder visible above the duvet) "
     "is tucked into bed with a soft purple-pink duvet covered in tiny stars. "
     "Through a round window the night sky shows a glowing crescent moon and stars. "
     "On the bedside table: a small lamp casting warm golden light, a stack of books. "
     "Warm purple-pink palette, very cosy, soft warm lighting. 16:9 cinematic composition. "
     "NO faces visible. NO text. NO captions."),

    ("scene2_star_arrives",
     "Pixar-style 3D animated illustration: same cosy children's bedroom at bedtime. "
     "A tiny glowing silver-gold star (about the size of a child's fist) has just floated down "
     "from the window and hovers above the pillow next to a child sleeping. "
     "The star has a soft warm glow that lights up the room with magic sparkles. "
     "Child seen from behind/side, face turned away, only the duvet and hair visible. "
     "Warm purple-pink palette, magical golden light from the star. 16:9 cinematic. "
     "NO visible face. NO text."),

    ("scene3_dreaming",
     "Pixar-style 3D animated illustration: same cosy children's bedroom at bedtime. "
     "A small child sleeping peacefully under a soft purple-pink duvet, only the back of "
     "the head visible on the pillow. The tiny glowing star is resting on the pillow next "
     "to the child, dimmed to a gentle nightlight glow. Soft musical notes drift in the air. "
     "Through the window: starry night sky, peaceful. Warm purple-pink palette, dreamy. "
     "16:9 cinematic. NO face visible. NO text."),
]


async def translate_story(master_en: str) -> dict[str, str]:
    """Translate the master story to all 12 non-English locales in one LLM batch."""
    from engine.llm_client import _call_llm
    prompt = (
        "Translate the following short bedtime fairy-tale excerpt to 12 languages. "
        "Keep the warm/magical tone. Names are localised: 'Lily' → use a natural child name "
        "appropriate for each language (e.g. de:'Mila', ja:'Yuki', ar:'Layla', etc.). "
        "Keep length within 20% of the source. Return ONLY valid JSON.\n\n"
        f"ENGLISH:\n{master_en}\n\n"
        "JSON keys: de, es, fr, it, pl, pt-BR, tr, ja, ko, ar, ru, uk"
    )
    resp = await _call_llm(
        system="You are an expert children's literature translator. Return only valid JSON.",
        user=prompt,
        purpose="story_demo_translate",
    )
    s = resp.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1]
        if s.startswith("json"):
            s = s[4:]
        s = s.strip("` \n")
    data = json.loads(s)
    out = {"en": master_en}
    for loc in LOCALES:
        if loc == "en":
            continue
        out[loc] = data.get(loc, master_en).strip()
    return out


async def tts_one(locale: str, text: str, out_path: Path):
    api = os.environ["ELEVENLABS_API_KEY"]
    vid, vname = CURATED_VOICES[locale]
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{vid}"
    payload = {
        "text": text,
        "model_id": "eleven_v3",
        "language_code": EL_LANG_CODE[locale],
        "voice_settings": {"stability": 0.50, "similarity_boost": 0.85, "style": 0.25},
    }
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=payload,
                          headers={"xi-api-key": api, "Content-Type": "application/json", "Accept": "audio/mpeg"},
                          timeout=aiohttp.ClientTimeout(total=120)) as r:
            if r.status != 200:
                body = (await r.read())[:300]
                raise RuntimeError(f"TTS {r.status}: {body!r}")
            out_path.write_bytes(await r.read())
    logger.info(f"[{locale}] TTS '{vname}' → {out_path.stat().st_size}B")


async def generate_scene(slug: str, prompt: str) -> Path:
    """Shared illustration across locales (same scene, no locale-specific text)."""
    img_path = WORK / f"{slug}.png"
    if img_path.exists():
        logger.info(f"  cached scene: {slug}")
        return img_path
    from engine.image_generator import _call_image_api
    data = await _call_image_api(
        content=[{"type": "text", "text": prompt}],
        scene_index=int(slug.lstrip("scene").split("_")[0]),
        style_label="painted",
        story_id=None,
    )
    if not data:
        raise RuntimeError(f"Image gen returned None for {slug}")
    img_path.write_bytes(data)
    logger.info(f"  generated scene: {slug} ({len(data)}B)")
    return img_path


async def make_video(locale: str, scenes: list[Path], audio: Path, out_path: Path):
    """Slideshow: each scene plays for an equal slice of audio with crossfade."""
    # Probe audio duration
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(audio),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    dur = float(stdout.decode().strip() or "25")
    n = len(scenes)
    per_scene = dur / n
    fade_dur = 0.8
    fade_out_start = max(0, dur - fade_dur)
    # Build filter graph: each image loops for per_scene seconds + ken-burns zoom
    inputs = []
    filter_parts = []
    for i, s in enumerate(scenes):
        inputs.extend(["-loop", "1", "-t", f"{per_scene:.3f}", "-i", str(s)])
    # Scale each + small zoom-pan
    for i in range(n):
        filter_parts.append(
            f"[{i}:v]scale=2400:-2,zoompan=z='min(zoom+0.0006,1.06)':"
            f"d={int(per_scene*24)}:s=1920x1080:fps=24[v{i}]"
        )
    # Crossfade chain
    if n == 1:
        filter_parts.append("[v0]null[vout]")
    else:
        chain = "[v0]"
        cur_time = per_scene
        for i in range(1, n):
            offset = cur_time - 0.6
            filter_parts.append(
                f"{chain}[v{i}]xfade=transition=fade:duration=0.6:offset={offset:.3f}[xf{i}]"
            )
            chain = f"[xf{i}]"
            cur_time += per_scene - 0.6
        filter_parts.append(f"{chain}fade=t=in:st=0:d={fade_dur},fade=t=out:st={fade_out_start}:d={fade_dur}[vout]")
    af = f"afade=t=in:st=0:d={fade_dur},afade=t=out:st={fade_out_start}:d={fade_dur}"
    filter_parts.append(f"[{n}:a]{af}[aout]")
    fc = ";".join(filter_parts)
    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-i", str(audio),
        "-filter_complex", fc,
        "-map", "[vout]", "-map", "[aout]",
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
        raise RuntimeError(f"ffmpeg failed: {stderr.decode()[-500:]}")
    logger.info(f"[{locale}] MP4 {out_path.stat().st_size}B duration={dur:.1f}s")


async def run_locale(locale: str, story_text: str, scenes: list[Path], force: bool):
    out_mp4 = STATIC / f"{locale}.mp4"
    if out_mp4.exists() and not force:
        logger.info(f"[{locale}] skip (exists)")
        return
    audio = WORK / f"{locale}.mp3"
    await tts_one(locale, story_text, audio)
    await make_video(locale, scenes, audio, out_mp4)


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--locale", help="run for one locale only")
    p.add_argument("--force", action="store_true")
    args = p.parse_args()
    targets = [args.locale] if args.locale else LOCALES
    logger.info(f"Generating story demos for: {targets}")

    # Translations
    tr_cache = WORK / "story_translations.json"
    if tr_cache.exists() and not args.force:
        translations = json.loads(tr_cache.read_text(encoding="utf-8"))
        logger.info(f"Using cached translations from {tr_cache}")
    else:
        logger.info("Translating master story…")
        translations = await translate_story(MASTER_STORY)
        tr_cache.write_text(json.dumps(translations, ensure_ascii=False, indent=2), encoding="utf-8")
    for loc in targets:
        snippet = translations[loc][:80]
        logger.info(f"  [{loc}] {snippet}…")

    # Shared illustrations (3 scenes used across all locales)
    logger.info("Generating 3 master scenes…")
    scenes = []
    for slug, prompt in SCENE_PROMPTS:
        scenes.append(await generate_scene(slug, prompt))

    # Per-locale TTS + ffmpeg
    for loc in targets:
        try:
            await run_locale(loc, translations[loc], scenes, args.force)
        except Exception as e:
            logger.error(f"[{loc}] FAILED: {e}", exc_info=True)
    logger.info(f"Done. Demos in {STATIC}")


if __name__ == "__main__":
    asyncio.run(main())
