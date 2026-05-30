"""Generate RSYA-ready banners for Skazik via Gemini-3-Pro-Image.

RSYA wants multiple aspect ratios. Generates square, vertical (mobile
story format), and landscape variants of the same concept.

Output: /app/assets/banners/banner_<n>_<aspect>.png
"""
import asyncio
import base64
import os
import sys
from pathlib import Path

import aiohttp

OUT = Path("/app/assets/banners")
OUT.mkdir(parents=True, exist_ok=True)
URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "google/gemini-3-pro-image-preview"

# Brand-consistent: soft purple palette, modern Pixar-quality, no real children's faces
BRIEF = (
    "Banner for Скази́к — Russian personalised children's bedtime story service. "
    "Always include: warm purple gradient palette (#7c5cff to #ff7eb6), "
    "soft cream highlights. Friendly, mature (not cartoonish), modern. "
    "No real children faces — only silhouettes, abstract characters, or scenery. "
    "Russian text appears legibly. Premium feel — comfortable for adult parents."
)

# Each entry: (id, prompt, aspect_ratio, image_size)
BANNERS = [
    # Square 1:1
    ("01_square",
     "Cozy night scene: silhouette of parent reading to a child by warm "
     "lamp light, soft purple glow, sparkles and crescent moon floating above. "
     "Above scene, bold Russian text: «Сказка про твоего ребёнка». Bottom "
     "right, smaller text: «skazik.app · 999 ₽». Modern flat illustration "
     "style. Square 1080×1080 composition.",
     "1:1", "1K"),

    # Vertical 4:5 (mobile feed)
    ("02_vertical",
     "Vertical mobile banner. Top: soft purple sky with crescent moon and "
     "stars. Middle: silhouette of a small child in pajamas looking up at "
     "the sky with wonder. Bottom: bold Russian text «Аудиосказка где "
     "герой — ваш ребёнок» on a soft cream card. Tiny CTA pill «Создать "
     "за 1 минуту» at the very bottom. Vertical 1080×1350.",
     "4:5", "1K"),

    # Landscape 16:9 (large RSYA placement)
    ("03_landscape",
     "Landscape banner. Left half: warm gradient purple background with "
     "floating crescent moon and 5 small sparkles. Right half: bold Russian "
     "wordmark on cream background: «Сказка по фото вашего малыша. Аудио, "
     "иллюстрации, видео.» with white CTA pill «Создать бесплатно». "
     "Landscape 16:9 composition.",
     "16:9", "1K"),

    # Square 1:1 alt - lifestyle/emotional
    ("04_square_alt",
     "Square banner. Centered illustration: a cute stylized cartoon fox "
     "(soft purple) sleeping curled around a crescent moon, on a cream "
     "background with soft floating sparkles. Top: small wordmark 'Сказик'. "
     "Bottom: bold Russian text «Тёплая сказка на ночь — за 5 минут». "
     "Square 1080×1080. Watercolor-modern style.",
     "1:1", "1K"),
]


async def call_image(session, model, prompt, aspect, size, key, timeout=120):
    payload = {
        "model": model,
        "modalities": ["image", "text"],
        "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
        "image_config": {"aspect_ratio": aspect, "image_size": size},
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    try:
        async with session.post(URL, json=payload, headers=headers,
                                timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            if r.status != 200:
                txt = await r.text()
                print(f"  HTTP {r.status}: {txt[:200]}")
                return None
            data = await r.json()
    except Exception as e:
        print(f"  err: {e}")
        return None
    msg = (data.get("choices") or [{}])[0].get("message", {})
    for img in (msg.get("images") or []):
        url = (img or {}).get("image_url", {}).get("url", "")
        if url.startswith("data:image"):
            return base64.b64decode(url.split(",", 1)[1])
    return None


async def main():
    key = os.environ["OPENROUTER_API_KEY"]
    async with aiohttp.ClientSession() as s:
        for bid, prompt, aspect, size in BANNERS:
            full = f"{BRIEF}\n\n{prompt}"
            print(f"→ {bid} ({aspect}) …")
            img = await call_image(s, MODEL, full, aspect, size, key)
            if img:
                out = OUT / f"banner_{bid}.png"
                out.write_bytes(img)
                print(f"  ✓ {out.name} ({len(img)}B)")
            else:
                print(f"  ✗ {bid} failed")


if __name__ == "__main__":
    asyncio.run(main())
