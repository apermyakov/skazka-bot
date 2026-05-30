"""Generate several synthetic 4yo Russian girl portraits via Gemini-3-Pro-Image.
User picks one — we then run the full story pipeline with it.
Output: /app/assets/kid_candidates/girl_<n>.png
"""
import asyncio
import base64
import os
from pathlib import Path

import aiohttp

OUT = Path("/app/assets/kid_candidates")
OUT.mkdir(parents=True, exist_ok=True)

API = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "google/gemini-3-pro-image-preview"

BASE = (
    "High-quality portrait photo of a synthetic (AI-generated, fictional) "
    "4-year-old Russian girl. Professional portrait photography, sharp focus "
    "on face, soft natural light, plain neutral background. Three-quarter "
    "face view, soft natural expression. Cute and friendly look. "
    "NOT a real child — this is a fictional sample. Square 1:1 composition."
)

# 6 variations — different hair, eyes, expression — so the user has real choice
VARIANTS = [
    ("01_blonde_curly",
     "Light blonde curly hair to her shoulders, big bright blue eyes, "
     "small button nose, gentle warm smile showing tiny teeth. Wearing a "
     "soft pink t-shirt. Cream background."),
    ("02_brunette_braids",
     "Dark brown hair in two neat braids tied with small white ribbons, "
     "warm hazel eyes, freckles across the nose, a slight curious half-smile. "
     "Wearing a pale yellow knitted sweater. Soft beige background."),
    ("03_redhead",
     "Reddish-auburn straight hair, bright green eyes, a few freckles, a "
     "wide cheerful smile. Wearing a mint-green dress with small white "
     "polka dots. Plain soft blue background."),
    ("04_brunette_short",
     "Short dark brown bob haircut with bangs, large dark brown eyes, calm "
     "thoughtful expression, gentle closed-mouth smile. Wearing a lavender "
     "cardigan over a white shirt. Plain off-white background."),
    ("05_blonde_straight",
     "Long straight ash-blonde hair, soft grey-blue eyes, a small dimple on "
     "her left cheek, a quiet shy smile. Wearing a sky-blue t-shirt. "
     "Soft pastel background."),
    ("06_brunette_curly",
     "Curly chestnut hair in soft waves down to her shoulders, warm brown "
     "eyes, rosy cheeks, a bright joyful smile. Wearing a coral pink "
     "long-sleeve. Cream background."),
]


async def gen_one(session, name, extra, key):
    full = BASE + "\n\n" + extra
    payload = {
        "model": MODEL, "modalities": ["image", "text"],
        "messages": [{"role": "user", "content": [{"type": "text", "text": full}]}],
        "image_config": {"aspect_ratio": "1:1", "image_size": "2K"},
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    try:
        async with session.post(API, json=payload, headers=headers,
                                timeout=aiohttp.ClientTimeout(total=180)) as r:
            if r.status != 200:
                print(f"  ✗ {name}: HTTP {r.status}: {(await r.text())[:200]}")
                return
            data = await r.json()
    except Exception as e:
        print(f"  ✗ {name}: {e}"); return
    msg = (data.get("choices") or [{}])[0].get("message", {})
    for img in (msg.get("images") or []):
        url = (img or {}).get("image_url", {}).get("url", "")
        if url.startswith("data:image"):
            png = base64.b64decode(url.split(",", 1)[1])
            out = OUT / f"girl_{name}.png"
            out.write_bytes(png)
            print(f"  ✓ {out.name} ({len(png)} bytes)")
            return
    print(f"  ✗ {name}: no image in response")


async def main():
    key = os.environ["OPENROUTER_API_KEY"]
    async with aiohttp.ClientSession() as s:
        # Parallel — 3 at a time so we don't overload
        sem = asyncio.Semaphore(3)
        async def with_sem(n, e):
            async with sem:
                await gen_one(s, n, e, key)
        await asyncio.gather(*(with_sem(n, e) for n, e in VARIANTS))


if __name__ == "__main__":
    asyncio.run(main())
