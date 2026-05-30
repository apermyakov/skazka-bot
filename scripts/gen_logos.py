"""Generate ~20 logo candidates for Skazik via OpenRouter.
Tries openai/gpt-image-1 first (per user request), falls back to
google/gemini-3-pro-image-preview if that route isn't enabled.

Run inside the web/bot container:
  docker compose exec -T web python /app/scripts/gen_logos.py
"""
import asyncio
import base64
import json
import os
import sys
import time
from pathlib import Path

import aiohttp

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OUT_DIR = Path("/app/assets/logo_candidates_gpt")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Try these models in order until one accepts our request shape
MODELS_TO_TRY = [
    "openai/gpt-5.4-image-2",
    "openai/gpt-image-1",
    "google/gemini-3-pro-image-preview",
]

BRAND_BRIEF = (
    "Brand: Скази́к (Skazik) — personalized children's bedtime story service. "
    "Audience: Russian parents (RU-speaking, ages 25–40). "
    "Vibe: warm, magical, gentle, modern. Not childish/cartoonish — a parent "
    "should feel comfortable showing it to friends. Palette: soft purples "
    "(#7c5cff, #ff7eb6), cream backgrounds, sparkle accents. Wordmark in "
    "Russian Cyrillic 'Сказик' is welcome but optional."
)

PROMPTS = [
    # Refined: variations on the strongest concepts from round 1
    # Wordmark + sparkle (clean, premium)
    "Minimalist Cyrillic wordmark logo 'Сказик' in soft purple #7c5cff, friendly modern geometric sans-serif (think Inter / SF Pro Display), single small sparkle replacing the dot on the letter 'и'. Clean white background. Centered, balanced, vector style. No effects, no shadows. Print-quality.",
    "Cyrillic wordmark 'Сказик' in deep violet #5a4cb8, refined modern sans-serif, with a tiny crescent moon hovering as accent above. White background. Generous whitespace. Premium, calm.",
    # Moon + sparkle icon + wordmark (the format that worked best in round 1)
    "Logo: small soft-purple crescent moon with a 4-point sparkle inside it, on the LEFT, and Cyrillic wordmark 'Сказик' in matching purple sans-serif on the RIGHT. Horizontal layout, vector, white background, balanced. Minimal.",
    "Logo: crescent moon icon in purple-to-pink gradient with a tiny sparkle, beside Cyrillic wordmark 'Сказик' in dark purple sans-serif. Modern, premium, vector. White background.",
    # Pure icon variants (for favicon and social avatar)
    "Just an icon, no text. Modern flat vector: a crescent moon cradling a small 4-point sparkle, in two-tone purple gradient (#7c5cff to #b08aff). White background. Symmetrical, balanced, square composition. Will be used as a favicon at 32x32 — must read clearly when small.",
    "Just an icon, no text. Geometric crescent moon with a single sparkle, in soft purple #7c5cff. Flat vector style. Cream background #fbf7ff. Will be used as an app icon at 192x192. Must be instantly recognizable.",
    # Sleeping cute animal (the strongest mascot in round 1)
    "Logo: gentle watercolor-style sleeping fox curled around a small crescent moon, soft pastel purples #7c5cff and creams. Cyrillic wordmark 'Сказик' below in matching purple modern sans-serif. Centered composition. Cream background. Cozy bedtime feel for parents (not too childish).",
    # Stars constellation
    "Cyrillic wordmark 'Сказик' in modern purple sans-serif, with 5 small stars connected by faint lines forming a gentle arch above the letters. Subtle, dreamy, premium feel. Cream background.",
    # Serif elegant (book-publisher vibe)
    "Cyrillic wordmark 'Сказик' in elegant high-contrast serif (like Playfair Display) in deep purple #5a4cb8, with a single small gold sparkle accent. Cream background, lots of whitespace. Children's book publisher aesthetic. Premium.",
    # Letter monogram approaches
    "Letter monogram logo: a stylized Cyrillic 'С' that doubles as a crescent moon, in soft purple-pink gradient. White background. Clean, modern, vector. Will be used as a square favicon. Just the monogram — no other text.",
    # Sparkle constellation icon
    "Just an icon, no text. Three small sparkles of varying sizes arranged in an upward triangle, in soft purple-pink gradient. White background. Vector style. Clean, magical, premium.",
    # Lullaby / soft cloud
    "Logo: soft round cloud with a small crescent moon resting on top, in pastel purple gradient. Below: Cyrillic wordmark 'Сказик' in matching purple sans-serif. Cozy bedtime feel. White background.",
    # Modern flat owl
    "Logo: simple geometric owl silhouette with eyes closed peacefully, in soft purple, perched on a crescent moon. Cyrillic wordmark 'Сказик' below in clean sans-serif. Cream background. Calm, mature, not cartoonish.",
    # Bedtime story emotional
    "Logo: minimalist silhouette of a parent holding a child, contained within a crescent moon shape, in soft purple gradient. Cyrillic wordmark 'Сказик' below in modern sans-serif. White background. Heartwarming, premium.",
    # Storybook open
    "Logo: simple line-art open book with a tiny crescent moon and 2-3 stars rising above the pages, in soft purple #7c5cff. Cyrillic wordmark 'Сказик' below in matching purple sans-serif. White background. Clean, vector.",
]


async def call_image_api(session: aiohttp.ClientSession, model: str, prompt: str,
                         api_key: str, timeout: int = 90) -> bytes | None:
    """One call against OpenRouter. Returns image bytes or None on failure."""
    payload = {
        "model": model,
        "modalities": ["image", "text"],
        "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
        "image_config": {"aspect_ratio": "1:1", "image_size": "1K"},
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        async with session.post(OPENROUTER_URL, json=payload, headers=headers,
                                timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            if r.status != 200:
                txt = await r.text()
                print(f"  [{model}] HTTP {r.status}: {txt[:200]}")
                return None
            data = await r.json()
    except Exception as e:
        print(f"  [{model}] error: {e}")
        return None
    # Try several response shapes (different models)
    msg = (data.get("choices") or [{}])[0].get("message", {})
    # 1) images array
    for img in (msg.get("images") or []):
        url = (img or {}).get("image_url", {}).get("url", "")
        if url.startswith("data:image"):
            return base64.b64decode(url.split(",", 1)[1])
    # 2) content list with image_url items
    for part in (msg.get("content") if isinstance(msg.get("content"), list) else []):
        if part.get("type") == "image_url":
            url = part.get("image_url", {}).get("url", "")
            if url.startswith("data:image"):
                return base64.b64decode(url.split(",", 1)[1])
    # 3) generations array (OpenAI shape)
    for g in (data.get("data") or []):
        b64 = g.get("b64_json")
        if b64:
            return base64.b64decode(b64)
    print(f"  [{model}] no image in response. keys: {list(msg.keys())}")
    return None


async def main():
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        print("Set OPENROUTER_API_KEY first."); sys.exit(1)

    # Probe which model works first using one quick prompt
    print("Probing models…")
    async with aiohttp.ClientSession() as s:
        chosen = None
        for m in MODELS_TO_TRY:
            print(f"  → {m}")
            img = await call_image_api(s, m, "A simple purple crescent moon icon, vector, white bg", api_key, timeout=60)
            if img:
                chosen = m
                print(f"  ✓ {m} works (probe got {len(img)}B)")
                break
        if not chosen:
            print("No image model accepted requests."); sys.exit(2)

        print(f"\nGenerating {len(PROMPTS)} logos with {chosen}…")
        # Slight concurrency to keep wall time reasonable
        sem = asyncio.Semaphore(3)
        results = [None] * len(PROMPTS)

        async def one(i, p):
            full = f"{BRAND_BRIEF}\n\nLogo prompt: {p}"
            async with sem:
                img = await call_image_api(s, chosen, full, api_key, timeout=120)
            if img:
                out = OUT_DIR / f"logo_{i+1:02d}.png"
                out.write_bytes(img)
                print(f"  ✓ #{i+1:02d} → {out.name} ({len(img)}B)")
                results[i] = str(out)
            else:
                print(f"  ✗ #{i+1:02d} failed")

        t0 = time.time()
        await asyncio.gather(*[one(i, p) for i, p in enumerate(PROMPTS)])
        elapsed = time.time() - t0

    ok = [r for r in results if r]
    print(f"\nDone in {elapsed:.0f}s. {len(ok)}/{len(PROMPTS)} succeeded.")
    print(f"Output: {OUT_DIR}")


if __name__ == "__main__":
    asyncio.run(main())
