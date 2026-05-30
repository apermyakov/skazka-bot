"""Generate 4 example photos (1 good + 3 bad) for the 'photo quality
tips' block on /create. Synthetic child — same artificial girl genre
so it doesn't look like stock or someone's real child.
"""
import asyncio
import base64
import os
from pathlib import Path

import aiohttp

OUT = Path("/app/web/static/photo_tips")
OUT.mkdir(parents=True, exist_ok=True)
API = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "google/gemini-3-pro-image-preview"

BASE = (
    "Photo of a synthetic (AI-generated, fictional) 4-year-old girl with "
    "dark brown shoulder-length hair, brown eyes, light skin. Same girl in "
    "all images for consistency. NOT a real child. Square 1:1 composition."
)

PHOTOS = [
    ("bad_group",
     "STRICTLY ONE PHOTO (not a grid, not a collage). Single photo of 3 "
     "small children playing together in a sandbox — our same dark-haired "
     "girl is one of them, but it's not obvious which one. All children are "
     "small in the frame, no single face stands out. Sunny day outdoors. "
     "NO text overlays. Single image, square 1:1."),
]


async def gen(s, name, prompt, key):
    full = BASE + "\n\n" + prompt
    payload = {"model": MODEL, "modalities": ["image", "text"],
               "messages": [{"role": "user", "content": [{"type": "text", "text": full}]}],
               "image_config": {"aspect_ratio": "1:1", "image_size": "1K"}}
    h = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    try:
        async with s.post(API, json=payload, headers=h,
                          timeout=aiohttp.ClientTimeout(total=120)) as r:
            if r.status != 200:
                print(f"  ✗ {name}: HTTP {r.status} {(await r.text())[:200]}"); return
            data = await r.json()
    except Exception as e:
        print(f"  ✗ {name}: {e}"); return
    msg = (data.get("choices") or [{}])[0].get("message", {})
    for img in (msg.get("images") or []):
        url = (img or {}).get("image_url", {}).get("url", "")
        if url.startswith("data:image"):
            png = base64.b64decode(url.split(",", 1)[1])
            (OUT / f"{name}.png").write_bytes(png)
            print(f"  ✓ {name} ({len(png)//1024} KB)"); return
    print(f"  ✗ {name}: no image returned")


async def main():
    key = os.environ["OPENROUTER_API_KEY"]
    async with aiohttp.ClientSession() as s:
        await asyncio.gather(*(gen(s, n, p, key) for n, p in PHOTOS))


if __name__ == "__main__":
    asyncio.run(main())
