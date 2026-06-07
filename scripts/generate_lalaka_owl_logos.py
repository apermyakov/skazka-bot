#!/usr/bin/env python3
"""Round 2: 10 logo concepts with owl INTEGRATED into the 'Lalaka' wordmark.

User wants creative typography where the owl is part of the letters,
not separate icon+text.

Cost ~$0.30. Wall time ~3-5 min sequential.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, "/app")

OUT = Path("/app/web/static/lalaka_logo_owl")
OUT.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("logo-owl-gen")

BRAND = (
    "Brand name: 'Lalaka' (always spelled L-a-l-a-k-a, no other letters, no typos). "
    "Children's audio fairy-tale product. Bedtime, warm, friendly, premium feel. "
    "Color palette: deep purple #7c5cff, warm pink #ff7eb6, white background. "
    "NEVER use Russian/Cyrillic characters. Logo only — no extra text. "
    "STRICT: this is wordmark + owl typography, NOT separate owl-icon + text. "
    "The owl must be PHYSICALLY PART OF one of the letters (replacing, inside, on top, integrated). "
)

CONCEPTS = [
    ("31", "owl_as_dot",
     "Wordmark 'Lalaka' followed by a stylised owl head replacing the period. "
     "The owl is small, round, deep purple with cream face-disc and big closed sleepy eyes. "
     "Bold rounded sans-serif typography for the letters. Modern, premium."),

    ("32", "owl_in_a_bowl",
     "Wordmark 'Lalaka' in bold rounded sans-serif. The bowl of the FIRST lowercase 'a' "
     "contains a tiny sleeping owl peeking out — owl is integrated INTO the letter shape, "
     "as if living inside the 'a'. Purple letters, pink owl accent."),

    ("33", "owl_eyes_double_o",
     "Wordmark 'Lalaka' in custom rounded display font. The two 'a' letters look like "
     "big round owl eyes — they have small pupils and a soft pink blush ring around each, "
     "so reading 'Lalaka' also reads as an owl face peeking. Purple+pink palette."),

    ("34", "owl_top_of_L",
     "Wordmark 'Lalaka'. A small cute sleeping owl is perched on top of the capital 'L', "
     "as if 'L' is a tree branch. Owl is purple with cream tummy. Bold modern sans-serif letters. "
     "Single horizontal lockup."),

    ("35", "L_is_owl",
     "Wordmark 'Lalaka' where the capital 'L' is replaced by a stylised owl silhouette "
     "whose body forms the vertical stroke and tail-wing forms the horizontal base. "
     "Rest of letters 'alaka' in matching bold rounded sans-serif. Purple+pink gradient."),

    ("36", "owl_hug_final_a",
     "Wordmark 'Lalaka' in friendly rounded sans-serif. A small sleeping owl with droopy wings "
     "is hugging/wrapping around the final 'a' from behind, peeking over its top. "
     "Purple letters, pink+cream owl. Cozy bedtime feel."),

    ("37", "ll_as_ears",
     "Wordmark 'Lalaka'. The 'l-l' (lowercase double L, between 'La' and 'aka') become "
     "two tall pointed owl ear tufts of a single small owl face nestled between them. "
     "Letters in purple bold sans-serif, owl in pink+cream. Clever wordplay."),

    ("38", "k_holds_owl",
     "Wordmark 'Lalaka' in bold rounded display font. The upper triangle/curve of 'k' "
     "cradles a tiny sleeping owl. Purple letters, soft pink+cream owl. "
     "Owl is PART of the 'k' construction."),

    ("39", "owl_winks_dot",
     "Wordmark 'Lalaka.' (with a period). The period after the wordmark is a small purple "
     "circle with two cream owl eyes inside it — one open, one winking. "
     "Friendly playful. Bold rounded sans-serif typography."),

    ("40", "owl_carved_in_L",
     "Wordmark 'Lalaka' where the inside negative space of the capital 'L' subtly forms "
     "a small owl silhouette in cream against the purple 'L'. Optical illusion logo. "
     "Bold display typography for the rest."),
]

NEG_INSTRUCT = (
    " STRICT: Show ONLY the logo, no mockups, no business cards, no scenes, no people. "
    "Single integrated wordmark composition where owl is FUSED with letters. "
    "Centered on a clean background (white). Square 1:1 composition. "
    "Crisp, ready-for-export vector-quality."
)


async def gen_one(num: str, slug: str, prompt: str, sem: asyncio.Semaphore):
    from engine.image_generator import _call_image_api
    full = BRAND + prompt + NEG_INSTRUCT
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
                logger.warning(f"[{num}] no data")
                return
            out_path.write_bytes(data)
            logger.info(f"[{num}] {slug} ✓ ({len(data)} B)")
        except Exception as e:
            logger.error(f"[{num}] failed: {e}")


async def main():
    sem = asyncio.Semaphore(4)
    await asyncio.gather(*[gen_one(num, slug, prompt, sem) for num, slug, prompt in CONCEPTS])
    n = sum(1 for f in OUT.iterdir() if f.suffix == ".png")
    logger.info(f"Done. {n} logos in {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
