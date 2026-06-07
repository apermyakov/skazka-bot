#!/usr/bin/env python3
"""Generate 11 photo→hero pairs (one per locale).

Each pair: (a) photorealistic studio portrait of a child with culturally
appropriate features, (b) Pixar 3D hero illustration of the SAME child face
in a cosy bedtime scene.

Output: /app/web/static/lalaka_examples/{locale}_photo.png
        /app/web/static/lalaka_examples/{locale}_hero.png

Cost: ~$0.88 (22 illustrations × $0.04). Time: ~10-15 min sequential.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, "/app")

OUT = Path("/app/web/static/lalaka_examples")
OUT.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("photo-hero")

# Per-locale child descriptions (culturally appropriate appearance).
# Same child described twice — once for photo style, once for Pixar style.
PROFILES = {
    "en": {
        "name": "Emma",
        "child": "a friendly 5-year-old child with light brown wavy shoulder-length hair, "
                 "warm hazel eyes, fair skin, a small button nose, gentle smile, wearing a "
                 "soft pink-purple knitted cardigan",
    },
    "de": {
        "name": "Mila",
        "child": "a cheerful 5-year-old German girl with blonde shoulder-length hair tied in "
                 "a small braid, bright blue eyes, fair skin with rosy cheeks, wearing a "
                 "cosy pink wool sweater",
    },
    "es": {
        "name": "Sofía",
        "child": "a warm 5-year-old Spanish girl with dark wavy brown hair, warm brown eyes, "
                 "olive skin tone, sun-kissed cheeks, wearing a soft cream and pink top",
    },
    "fr": {
        "name": "Chloé",
        "child": "an elegant 5-year-old French girl with chestnut shoulder-length hair, soft "
                 "blue-green eyes, fair skin, delicate features, wearing a Breton-stripe blue "
                 "and white shirt with a tiny pink ribbon in hair",
    },
    "it": {
        "name": "Sofia",
        "child": "a warm 5-year-old Italian girl with dark wavy brown hair, expressive brown "
                 "eyes, warm Mediterranean skin tone, wearing a cosy peach knit",
    },
    "pl": {
        "name": "Zosia",
        "child": "a sweet 5-year-old Polish girl with light blonde shoulder-length hair, "
                 "clear blue eyes, fair skin, wearing a traditional-inspired warm pink woollen "
                 "vest over a white blouse",
    },
    "pt-BR": {
        "name": "Alice",
        "child": "a joyful 5-year-old Brazilian girl with curly chestnut-brown hair, warm "
                 "brown eyes, golden-brown skin tone, bright smile, wearing a cheerful yellow "
                 "and pink top",
    },
    "tr": {
        "name": "Elif",
        "child": "a thoughtful 5-year-old Turkish girl with dark long hair held back by a "
                 "small clip, deep brown eyes, warm beige skin tone, wearing a soft mint and "
                 "pink top",
    },
    "ja": {
        "name": "Yuki",
        "child": "a calm 5-year-old Japanese girl with straight black bobbed hair with a "
                 "small clip, dark brown almond-shaped eyes, fair skin with pink cheeks, "
                 "wearing a soft pink kawaii sweater",
    },
    "ko": {
        "name": "Bomi",
        "child": "a curious 5-year-old Korean girl with straight black shoulder-length hair "
                 "tied in two small pigtails, dark eyes, fair skin, wearing a soft pastel "
                 "lavender hoodie",
    },
    "ar": {
        "name": "Layla",
        "child": "a graceful 5-year-old Middle-Eastern girl with dark wavy long hair, large "
                 "expressive deep brown eyes with long lashes, warm light-brown skin tone, "
                 "wearing a soft pink and gold dress",
    },
}

PHOTO_STYLE = (
    "Photorealistic studio portrait photo of {child}. Soft natural window light, neutral "
    "warm cream background. Centered head-and-shoulders crop. Looking warmly toward camera. "
    "High-quality DSLR photography style. Square 1:1 composition. "
    "STRICT: photorealistic real-photo style, NOT illustrated, NOT cartoon, NOT 3D. "
    "Single child only. No text. No watermark."
)

HERO_STYLE = (
    "Pixar-style 3D animated illustration of {child} as the hero of a bedtime fairy tale. "
    "She is sitting on the edge of a cosy bed in a children's bedroom at bedtime, smiling "
    "gently. A tiny glowing silver-gold magical star hovers above her shoulder. Warm "
    "purple-pink palette, soft golden light from a bedside lamp. Through a round window: "
    "starry night sky with crescent moon. Pixar 3D rendering quality, expressive face, "
    "EXACT same hair colour/style, eye colour, skin tone and overall features as described. "
    "Square 1:1 composition. No text. Single child."
)


async def gen_one(locale: str, kind: str, prompt: str, sem: asyncio.Semaphore):
    out_path = OUT / f"{locale}_{kind}.png"
    if out_path.exists():
        logger.info(f"  [{locale}/{kind}] cached")
        return
    from engine.image_generator import _call_image_api
    async with sem:
        try:
            data = await _call_image_api(
                content=[{"type": "text", "text": prompt}],
                scene_index=0,
                style_label="logo" if kind == "photo" else "painted",
                story_id=None,
            )
            if not data:
                logger.warning(f"  [{locale}/{kind}] no data")
                return
            out_path.write_bytes(data)
            logger.info(f"  [{locale}/{kind}] ✓ ({len(data)}B)")
        except Exception as e:
            logger.error(f"  [{locale}/{kind}] failed: {e}")


async def main():
    sem = asyncio.Semaphore(4)
    tasks = []
    for loc, prof in PROFILES.items():
        child = prof["child"]
        tasks.append(gen_one(loc, "photo", PHOTO_STYLE.format(child=child), sem))
        tasks.append(gen_one(loc, "hero",  HERO_STYLE.format(child=child), sem))
    await asyncio.gather(*tasks)
    n = sum(1 for f in OUT.iterdir() if f.suffix == ".png" and ("_photo" in f.name or "_hero" in f.name))
    logger.info(f"Done. {n} files in {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
