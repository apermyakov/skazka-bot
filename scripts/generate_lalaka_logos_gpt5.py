#!/usr/bin/env python3
"""Generate 10 Lalaka logo concepts via OpenAI GPT-5.4 Image 2 (latest, via OpenRouter).

Same brand constraints + concepts as the owl-integrated round, but routed
through GPT-5.4 Image which usually has stronger typography handling
than Gemini.

Cost: ~$0.04-0.10 each × 10 = ~$0.50.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, "/app")

import aiohttp

OUT = Path("/app/web/static/lalaka_logo_gpt5")
OUT.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("logo-gpt5")

MODEL = "openai/gpt-5.4-image-2"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

BRAND = (
    "Brand name: 'Lalaka' (always spelled L-a-l-a-k-a — six letters, no other letters, no typos). "
    "Children's audio fairy-tale product. Bedtime, warm, friendly, premium feel. "
    "Color palette: deep purple #7c5cff, warm pink #ff7eb6, white background. "
    "NEVER use Russian/Cyrillic. Logo only — no extra text. "
)

CONCEPTS = [
    # Wordmarks — premium typography focus (where GPT-5 should beat Gemini)
    ("g01", "premium_wordmark",
     "Premium minimalist wordmark logo 'Lalaka' in custom-drawn rounded display sans-serif. "
     "Bold lowercase letters, deep purple #2A1B6B color. The dot at the end is a small "
     "perfect circle in coral pink. Clean white background. Designed by a top-tier branding studio. "
     "Stripe-quality typography. Square 1:1."),

    ("g02", "warm_wordmark",
     "Warm hand-crafted wordmark 'Lalaka' in a custom humanist sans-serif with slightly playful "
     "rounded terminals. Deep purple text on white. Subtle pink heart-shape replaces the dot "
     "of the lowercase letters where dot would naturally sit. Cosy bedtime feel. Square."),

    # Letter L marks
    ("g03", "L_monogram_circle",
     "Logo: capital letter 'L' in white, set inside a vibrant purple-to-pink gradient circle. "
     "App-icon style. The L is bold, modern, slightly rounded. Single mark, no wordmark. "
     "Centered on white background. Square 1:1."),

    ("g04", "L_with_pink_dot",
     "Logo: clean modern capital 'L' in deep purple, with a small coral pink circle dot "
     "positioned at the bottom-right where a period would go. Minimal, premium, monogram-style. "
     "Square 1:1 white background."),

    # Owl integrated (retry — GPT-5 might do this better)
    ("g05", "owl_perched_L",
     "Logo combo: capital 'L' on the left in deep purple, a small stylised owl with closed "
     "sleepy eyes perched on top of the L like on a tree branch. Owl in coral pink with cream "
     "tummy. Wordmark 'Lalaka' to the right of the L in matching purple sans-serif. "
     "Single horizontal lockup, premium quality."),

    # Soft character
    ("g06", "sleeping_moon_glyph",
     "Logo: a single elegant glyph — a soft crescent moon shape morphed into the letter L. "
     "Gradient deep purple to soft pink. The crescent forms the L's curve. Minimalist, "
     "ownable, distinct. No text. Square 1:1 white background."),

    # Lullaby concept
    ("g07", "musical_la_la",
     "Logo: stylised wordmark 'Lalaka' where the two 'a' letters have a tiny musical note "
     "rising from their tops, suggesting the 'la la la' of a lullaby. Deep purple letters, "
     "coral pink notes. Clean rounded sans-serif. Subtle, premium."),

    # Negative space cleverness
    ("g08", "negative_space_star",
     "Logo: capital 'L' in deep purple with a small star-shape carved out (negative space) "
     "in the corner where the L's vertical meets horizontal. Coral pink shows through the star. "
     "Clever, minimal, monogram. White background."),

    # Photographic abstract symbol (Airbnb bélo style)
    ("g09", "abstract_belo",
     "Logo: a single original abstract symbol made of one continuous flowing line — "
     "suggesting a sleeping child, a parent's embrace, and a story all at once. "
     "Purple-to-pink gradient, soft curves. Like Airbnb's bélo but original. "
     "Ownable, premium, no letters. Square 1:1 white background."),

    # Hand-lettered
    ("g10", "handwritten_warm",
     "Logo: wordmark 'Lalaka' in a custom warm hand-lettered script — looks like a parent "
     "wrote it lovingly with a felt-tip marker. Deep purple ink with subtle texture. "
     "Tiny coral pink heart above one of the i-style dots. Premium feel — not amateur scribble. "
     "Square 1:1 white background."),
]


async def gen_one(num: str, slug: str, prompt: str, sem: asyncio.Semaphore):
    out_path = OUT / f"{num}_{slug}.png"
    if out_path.exists():
        logger.info(f"[{num}] skip (exists)")
        return
    full = BRAND + " " + prompt + " STRICT: produce ONLY the logo, centered, on a square clean background."
    body = {
        "model": MODEL,
        "modalities": ["image", "text"],
        "messages": [{"role": "user", "content": full}],
    }
    api = os.environ["OPENROUTER_API_KEY"]
    async with sem:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(OPENROUTER_URL, json=body,
                                  headers={"Authorization": f"Bearer {api}"},
                                  timeout=aiohttp.ClientTimeout(total=180)) as r:
                    data = await r.json()
            if r.status != 200:
                logger.error(f"[{num}] HTTP {r.status}: {data}")
                return
            # Extract image from response
            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message", {})
            images = msg.get("images") or []
            if not images:
                logger.warning(f"[{num}] no images in response. content={str(msg)[:200]}")
                return
            # Each image is {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
            img_url = (images[0].get("image_url") or {}).get("url", "")
            if img_url.startswith("data:image"):
                b64 = img_url.split(",", 1)[1]
                out_path.write_bytes(base64.b64decode(b64))
                logger.info(f"[{num}] {slug} ✓ ({out_path.stat().st_size} B)")
            else:
                logger.warning(f"[{num}] unexpected image URL format: {img_url[:80]}")
        except Exception as e:
            logger.error(f"[{num}] failed: {e}", exc_info=True)


async def main():
    sem = asyncio.Semaphore(3)  # gentle concurrency
    await asyncio.gather(*[gen_one(num, slug, prompt, sem) for num, slug, prompt in CONCEPTS])
    n = sum(1 for f in OUT.iterdir() if f.suffix == ".png")
    logger.info(f"Done. {n} logos in {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
