#!/usr/bin/env python3
"""Generate 30 logo concepts for Lalaka via Gemini Pro Image.

Outputs to web/static/lalaka_logo_options/{NN}_{slug}.png.
Each concept square 1024x1024, transparent or solid backdrop.

Cost: ~$1.20 for 30 images at Gemini Pro Image rates.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, "/app")

OUT = Path("/app/web/static/lalaka_logo_options")
OUT.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("logo-gen")

BRAND = (
    "Brand name: 'Lalaka' (always spelled L-a-l-a-k-a, no other letters, no typos). "
    "Children's audio fairy-tale product. Bedtime, warm, friendly, premium feel. "
    "Color palette: deep purple #7c5cff, warm pink #ff7eb6, white background acceptable. "
    "NEVER use Russian/Cyrillic characters. Logo only — no other text."
)

CONCEPTS = [
    ("01", "wordmark_clean", "Clean modern minimalist wordmark logo 'Lalaka' in custom rounded sans-serif typography, deep purple letters on white background, slight playful curve, balanced spacing. Premium, professional."),
    ("02", "wordmark_dreamy", "Dreamy soft wordmark logo 'Lalaka' in custom soft handwritten cursive script, purple-to-pink gradient. Lowercase. Trailing flourish on final 'a'. Cosy bedtime children's brand."),
    ("03", "wordmark_dot", "Wordmark logo 'Lalaka.' (with a period after) in bold rounded sans-serif, deep purple text, period in coral pink. Tight kerning. Symmetric. White background."),
    ("04", "wordmark_moon_dot", "Wordmark logo 'Lalaka' in clean modern sans-serif. Replace the period after Lalaka with a small crescent moon icon. Purple text, white moon-dot."),
    ("05", "wordmark_star_a", "Wordmark logo 'Lalaka' in friendly rounded display font. The two 'a' letters have tiny stars inside their bowls. Purple text on cream background."),
    ("06", "letter_L_moon", "Letter mark logo: single capital 'L' incorporating a crescent moon forming the bottom horizontal stroke. Purple gradient. Clean, modern. Circular badge."),
    ("07", "letter_L_speech", "Letter mark logo: capital 'L' shaped like a speech bubble. White 'L' on purple gradient circle. Children's storytelling vibe."),
    ("08", "letter_L_book", "Letter mark logo: capital 'L' designed as an open book viewed from the side. Purple ink + cream pages. Standalone icon, square format."),
    ("09", "letter_L_simple", "Geometric letter 'L' lockup, modern sans-serif heavy stroke, gradient purple-to-pink, sitting on a soft pastel circle. Centered."),
    ("10", "letter_L_handwritten", "Hand-drawn whimsical lowercase 'l' with a heart at the top. Purple ink on cream paper texture. Cozy children's book illustration style."),
    ("11", "icon_moon_text", "Logo combo: crescent moon with a small star inside it (icon on left), then wordmark 'Lalaka' (right) in modern sans-serif. Purple ink. White background. Horizontal layout."),
    ("12", "icon_pillow_text", "Logo combo: small fluffy cloud-pillow icon (left) + 'Lalaka' wordmark (right) in rounded sans-serif. Pink+purple palette. Children's bedtime."),
    ("13", "icon_book_text", "Logo combo: small open storybook icon with sparkles (left) + 'Lalaka' wordmark in friendly sans-serif (right). Warm purple+gold. White background."),
    ("14", "icon_owl_text", "Logo combo: minimalist cute owl head silhouette (left) + 'Lalaka' wordmark (right). Purple+cream. Modern minimal."),
    ("15", "icon_bunny_text", "Logo combo: tiny sleeping bunny silhouette on a crescent moon (left) + 'Lalaka' wordmark in rounded sans-serif (right). Soft purple. Children's brand."),
    ("16", "abstract_swirl", "Abstract logo symbol: swirling ribbon forming a stylized 'L' shape, suggesting a bedtime story unfurling. Purple-to-pink gradient. Standalone icon."),
    ("17", "abstract_star", "Abstract logo: 5-pointed star with one elongated bottom point forming an 'L' arm. Purple+pink. Modern flat design. Centered on white."),
    ("18", "abstract_zzz", "Abstract logo: stylized 'z-z-z' sleepy letters morphing into a crescent moon. Soft purple+pink. Children's bedtime brand."),
    ("19", "abstract_speech", "Abstract logo: a soft cloud-shaped speech bubble with a crescent moon tail (instead of pointer). Purple+pink gradient. Standalone icon."),
    ("20", "abstract_lullaby_wave", "Abstract logo: gentle sound-wave lines emerging from a small moon, suggesting a lullaby. Purple+pink. Minimalist."),
    ("21", "char_moonface", "Friendly character logo: smiling crescent moon with closed sleepy eyes, soft purple, blushing pink cheeks. Round, child-friendly mascot face. White background."),
    ("22", "char_starbear", "Cute character logo: tiny bear cub holding a star, eyes closed peacefully, soft purple+cream colors. Logo-clean, white background."),
    ("23", "char_dreambunny", "Cute character logo: small bunny with droopy ears hugging a tiny crescent moon. Soft purple+pink. Standalone mascot, round composition."),
    ("24", "char_owl_book", "Cute character logo: stylized owl perched on a tiny open book with a star above it. Purple+gold. Children's literature brand mark."),
    ("25", "char_smiling_cloud", "Cute character logo: smiling fluffy cloud with closed eyes and stars trailing behind. Soft purple+pink. Round, balanced."),
    ("26", "badge_circle_L", "Badge logo: capital 'L' inside a circle, gradient purple-to-pink border, white center. Modern, app-icon style. Square format."),
    ("27", "badge_moonshield", "Shield-style badge logo: crescent moon emblem on a soft purple shield with star accents. Wordmark 'Lalaka' beneath in small caps. White background."),
    ("28", "badge_starsquircle", "Squircle badge with a single 5-point star and small Lalaka text beneath. Purple+pink gradient. App-icon ready."),
    ("29", "badge_diamond", "Diamond-shaped badge enclosing a stylized 'L' with a moon accent. Purple gradient. Centered, premium feel."),
    ("30", "badge_pillow_L", "Soft rounded pillow-shape badge in purple+pink gradient, enclosing a clean white capital 'L'. App-icon ready, cosy children's brand."),
]

NEG_INSTRUCT = (
    " STRICT: Show ONLY the logo, no mockups, no business cards, no scenes, no people. "
    "No filler text other than the brand name 'Lalaka' (when applicable). "
    "Centered on a clean background (white or transparent). "
    "Square 1:1 composition. Crisp, ready-for-export vector-quality."
)


async def gen_one(num: str, slug: str, prompt: str, sem: asyncio.Semaphore):
    from engine.image_generator import _call_image_api
    full = BRAND + " " + prompt + NEG_INSTRUCT
    out_path = OUT / f"{num}_{slug}.png"
    if out_path.exists():
        logger.info(f"[{num}] skip (exists)")
        return
    async with sem:
        try:
            data = await _call_image_api(
                content=[{"type": "text", "text": full}],
                scene_index=int(num),
                style_label="logo",
                story_id=None,
            )
            if not data:
                logger.warning(f"[{num}] no data returned")
                return
            out_path.write_bytes(data)
            logger.info(f"[{num}] {slug} ✓ ({len(data)} B)")
        except Exception as e:
            logger.error(f"[{num}] failed: {e}")


async def main():
    sem = asyncio.Semaphore(4)
    await asyncio.gather(*[gen_one(num, slug, prompt, sem) for num, slug, prompt in CONCEPTS])
    logger.info(f"Done. {sum(1 for f in OUT.iterdir() if f.suffix=='.png')} logos in {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
