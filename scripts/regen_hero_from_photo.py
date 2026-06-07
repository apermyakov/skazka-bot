#!/usr/bin/env python3
"""Re-generate the 11 hero illustrations using the photo as REFERENCE input.

Previous run generated hero descriptively (same hair colour, etc.) so the faces
didn't match. This run passes the photo as image_url to the same image API —
identical workflow to what the real Lalaka product does when a user uploads
their child's photo.

Output overwrites: /app/web/static/lalaka_examples/{locale}_hero.png
"""
from __future__ import annotations

import asyncio
import base64
import logging
import sys
from pathlib import Path

sys.path.insert(0, "/app")

OUT = Path("/app/web/static/lalaka_examples")

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("regen-hero")

LOCALES = ["en", "de", "es", "fr", "it", "pl", "pt-BR", "tr", "ja", "ko", "ar"]

HERO_PROMPT = (
    "Re-imagine the child from the reference photo as the hero of a bedtime fairy tale, "
    "in CLASSIC HAND-PAINTED FAIRY-TALE STORYBOOK style — rich gouache and oil painting "
    "with visible brushwork, warm golden light, painterly textures, the timeless look of "
    "a treasured children's picture book. Painterly, NOT flat vector, NOT photographic, "
    "NOT Pixar 3D, NOT digital cartoon. Think classic European children's-book illustration.\n\n"
    "The child's face must be RECOGNISABLY the SAME child from the photo — same hair "
    "colour/style, same eye colour, same skin tone, same facial features (nose, mouth, smile), "
    "same age.\n\n"
    "Scene: the child is sitting on the edge of a cosy bed in a children's bedroom at "
    "bedtime, smiling gently toward the viewer. A tiny glowing silver-gold magical star "
    "hovers above her shoulder. Warm purple-pink palette, soft golden light from a bedside "
    "lamp. Through a round window: starry night sky with crescent moon. Single child only.\n\n"
    "STRICT: classic painted-storybook style with visible brushwork. Preserve the face from "
    "the reference photo. NO text, NO captions. Square 1:1 composition."
)


async def regen_hero(locale: str, sem: asyncio.Semaphore):
    photo_path = OUT / f"{locale}_photo.png"
    hero_path = OUT / f"{locale}_hero.png"
    if not photo_path.exists():
        logger.warning(f"  [{locale}] no photo, skip")
        return
    photo_b64 = base64.b64encode(photo_path.read_bytes()).decode("ascii")
    from engine.image_generator import _call_image_api
    content = [
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{photo_b64}"}},
        {"type": "text", "text": HERO_PROMPT},
    ]
    async with sem:
        try:
            data = await _call_image_api(
                content=content,
                scene_index=0,
                style_label="painted",
                story_id=None,
            )
            if not data:
                logger.warning(f"  [{locale}] no data returned")
                return
            hero_path.write_bytes(data)
            logger.info(f"  [{locale}] ✓ ({len(data)}B)")
        except Exception as e:
            logger.error(f"  [{locale}] failed: {e}")


async def main():
    sem = asyncio.Semaphore(4)
    await asyncio.gather(*[regen_hero(loc, sem) for loc in LOCALES])
    logger.info("done")


if __name__ == "__main__":
    asyncio.run(main())
