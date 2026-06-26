"""Generate 5 candidate spoken-intro variations for Skazik stories.

Each variant tries a different "brand stamp" pattern: minimal-title-only,
classic "X presents", direct-warm, mini-narrative, bedtime-whisper. They all
use the same warm female narrator voice (Marina_EL) so the only thing being
compared is the COPY + the ElevenLabs v3 audio tags.

Saves each MP3 under /app/media/_intro_demos/v{N}.mp3 — publicly reachable at
https://skazik.app/media/_intro_demos/v{N}.mp3.

Run:
  docker exec skazka-bot python /app/scripts/intro_variants.py
"""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, "/app")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("intro-variants")

OUT_DIR = Path("/app/media/_intro_demos")
NARRATOR_VOICE_ID = "ymDCYd8puC7gYjxIamPt"  # Marina_EL — warm female narrator

TITLE = "Ваня и лунный котёнок"

VARIANTS = [
    {
        "key": "v1_title_only",
        "comment": "Только название, протяжное «сказка», без бренда",
        "text": "[slows down] [mysterious] Ска-а-а-зка... [long pause] Ваня и лунный котёнок.",
    },
    {
        "key": "v2_brand_presents",
        "comment": "Классика Disney — «Сказик представляет»",
        "text": "[slows down] [soft] Ска-а-азик... представляет. [long pause] [mysterious] Ваня и лунный котёнок.",
    },
    {
        "key": "v3_direct_warm",
        "comment": "Прямое тёплое обращение к ребёнку",
        "text": "[soft] [slows down] Слушай, маленький... [pause] эта сказка только для тебя. [long pause] [whispers] Ваня и лунный котёнок.",
    },
    {
        "key": "v4_mini_narrative",
        "comment": "Мини-сюжет — «в библиотеке Сказика»",
        "text": "[slows down] [mysterious] В волшебной библиотеке Ска-а-азика... [pause] нашлась новая история. [long pause] Ваня. [pause] И лунный котёнок.",
    },
    {
        "key": "v5_bedtime_whisper",
        "comment": "Колыбельный шёпот, сразу bedtime-mood",
        "text": "[whispers] [slows down] Тс-с-с... [pause] [soft] начинается сказка. [long pause] [whispers] Ваня и лунный котёнок.",
    },
]


async def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    from engine import tts_client
    from db.config_manager import cfg
    def_stab = float(await cfg.get("tts.default_stability", 0.45))
    def_sim = float(await cfg.get("tts.default_similarity", 0.80))
    def_style = float(await cfg.get("tts.default_style", 0.25))

    segments = []
    for v in VARIANTS:
        segments.append({
            "text": v["text"],
            "voice_id": NARRATOR_VOICE_ID,
            "stability": def_stab,
            "similarity": def_sim,
            "style": def_style,
            "language_code": "ru",
        })

    log.info("synthesizing %d variants…", len(segments))
    results = await tts_client.synthesize_batch(segments, max_concurrent=3)

    for v, audio in zip(VARIANTS, results):
        if not audio:
            log.warning("variant %s: TTS empty", v["key"])
            continue
        path = OUT_DIR / f"{v['key']}.mp3"
        path.write_bytes(audio)
        log.info("  %s → %s (%d KB) — %s", v["key"], path, len(audio) // 1024, v["comment"])

    print()
    print("=== Listen via these URLs: ===")
    for v in VARIANTS:
        print(f"  {v['key']:<24} https://skazik.app/media/_intro_demos/{v['key']}.mp3")
        print(f"  {'':>24}   ↳ {v['comment']}")


if __name__ == "__main__":
    asyncio.run(main())
