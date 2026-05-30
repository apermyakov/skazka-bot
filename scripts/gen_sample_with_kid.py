"""Generate a synthetic 4yo child portrait via Gemini-3-Pro-Image (Nano Banana Pro),
then run the full production pipeline using that as the reference photo.

Outputs go to web/static/sample/ replacing the previous demo.

Cost: ~$0.30 ($0.03 for child portrait + $0.25 for full pipeline).
"""
import asyncio
import base64
import json
import os
import shutil
import sys
from pathlib import Path

import aiohttp

sys.path.insert(0, "/app")

OUT_DIR = Path("/app/web/static/sample")
OUT_DIR.mkdir(parents=True, exist_ok=True)
CHILD_REF = OUT_DIR / "child_reference.png"

# Synthetic kid: name + story context. Neutral Russian name, gender-flexible feel.
CHILD_NAME = "Тёма"
CHILD_AGE = 4
DEMO_CONTEXT = (
    f"Сказка про мальчика {CHILD_NAME}, ему {CHILD_AGE} года. "
    "Он очень любознательный, но иногда боится темноты в спальне. "
    "Однажды ночью к нему в окно прилетает добрый светлячок Лучик, "
    "и они вместе отправляются в маленькое приключение по комнате — "
    "поговорить с игрушками, послушать тишину дома, увидеть, что "
    "темнота — не страшная, а уютная и сонная."
)

CHILD_PROMPT = (
    "Generate a high-quality portrait photo of a synthetic (AI-generated, "
    "fictional) 4-year-old Russian boy named Тёма. Three-quarter face view, "
    "looking at camera with a soft warm smile, gentle curious eyes. "
    "Light brown hair, slightly tousled. Wearing a plain pastel-blue t-shirt. "
    "Neutral soft beige studio background. Soft natural daylight from the "
    "left. Professional portrait photography style, sharp focus on face, "
    "shallow background blur. NOT a real child — this is a synthetic "
    "character for a sample illustration. Square 1:1 composition."
)


async def gen_child_portrait(session):
    api_key = os.environ["OPENROUTER_API_KEY"]
    payload = {
        "model": "google/gemini-3-pro-image-preview",
        "modalities": ["image", "text"],
        "messages": [{"role": "user", "content": [{"type": "text", "text": CHILD_PROMPT}]}],
        "image_config": {"aspect_ratio": "1:1", "image_size": "2K"},  # 2K for ref quality
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    print("→ generating synthetic child portrait (2K)…")
    async with session.post(
        "https://openrouter.ai/api/v1/chat/completions",
        json=payload, headers=headers,
        timeout=aiohttp.ClientTimeout(total=180),
    ) as r:
        if r.status != 200:
            print(f"  HTTP {r.status}: {(await r.text())[:300]}")
            return None
        data = await r.json()
    msg = (data.get("choices") or [{}])[0].get("message", {})
    for img in (msg.get("images") or []):
        url = (img or {}).get("image_url", {}).get("url", "")
        if url.startswith("data:image"):
            return base64.b64decode(url.split(",", 1)[1])
    print(f"  no image: {list(msg.keys())}")
    return None


async def main():
    # Step 1: Generate child portrait
    async with aiohttp.ClientSession() as s:
        png = await gen_child_portrait(s)
    if not png:
        print("FAIL: child portrait"); return
    CHILD_REF.write_bytes(png)
    print(f"  ✓ saved {CHILD_REF.name} ({len(png)} bytes)")

    # Step 2: Run full pipeline with this photo as reference
    from engine.pipeline import generate_fairytale
    ref_b64 = base64.b64encode(png).decode("ascii")

    print(f"\n→ running full pipeline (~10 min)…")
    async def progress(msg):
        safe = msg.encode("ascii", "replace").decode("ascii")
        print(f"  [{safe}]")

    result = await generate_fairytale(
        context=DEMO_CONTEXT,
        reference_photo_b64=ref_b64,
        on_status=progress,
    )

    print(f"\nGenerated:")
    print(f"  Title: {result['title']}")
    print(f"  Duration: {result['duration']:.1f}s")
    print(f"  Audio: {result['file_path']}")
    if result.get("video_path"):
        print(f"  Video: {result['video_path']}")
    if result.get("illustration_paths"):
        print(f"  Illustrations: {len(result['illustration_paths'])}")

    # Step 3: Replace previous sample artifacts
    # Clean old scene_*.webp / png
    for old in OUT_DIR.glob("scene_*"):
        old.unlink()
    if (OUT_DIR / "sample.mp3").exists():
        (OUT_DIR / "sample.mp3").unlink()
    if (OUT_DIR / "sample.mp4").exists():
        (OUT_DIR / "sample.mp4").unlink()

    shutil.copy(result["file_path"], OUT_DIR / "sample.mp3")
    print(f"  → sample.mp3")
    if result.get("video_path"):
        shutil.copy(result["video_path"], OUT_DIR / "sample.mp4")
        print(f"  → sample.mp4")
    if result.get("illustration_paths"):
        for i, p in enumerate(result["illustration_paths"]):
            shutil.copy(p, OUT_DIR / f"scene_{i+1}.png")
            print(f"  → scene_{i+1}.png")

    # meta.json
    (OUT_DIR / "meta.json").write_text(json.dumps({
        "title": result["title"],
        "duration": result["duration"],
        "context": DEMO_CONTEXT,
        "child": {"name": CHILD_NAME, "age": CHILD_AGE, "synthetic": True},
    }, ensure_ascii=False, indent=2))
    print(f"  → meta.json")


if __name__ == "__main__":
    asyncio.run(main())
