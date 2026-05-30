"""Generate a complete demo story for the landing page.
Runs full pipeline (text + screenplay + TTS + illustrations + video).
~10 min, ~$0.25, neutral content (no real child names).
Output goes into web/static/sample/ for serving as a permanent demo.
"""
import asyncio
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, "/app")

DEMO_CONTEXT = (
    "Сказка про маленького дракончика Огонёк, ему 4 года, он очень любит "
    "приключения. Сегодня он впервые отправляется в волшебный лес и встречает "
    "там сову-учительницу и весёлую белочку. Они вместе помогают потерявшейся "
    "звёздочке вернуться на небо. Добрая сказка про дружбу и смелость."
)

OUT_DIR = Path("/app/web/static/sample")
OUT_DIR.mkdir(parents=True, exist_ok=True)


async def main():
    from engine.pipeline import generate_fairytale
    print(f"Generating sample story… (~10 min)")

    async def progress(msg):
        safe = msg.encode("ascii", "replace").decode("ascii")
        print(f"  [{safe}]")

    result = await generate_fairytale(context=DEMO_CONTEXT, on_status=progress)

    print(f"\nGenerated:")
    print(f"  Title: {result['title']}")
    print(f"  Duration: {result['duration']:.1f}s")
    print(f"  Segments: {result['segments_count']}")
    print(f"  Audio: {result['file_path']}")
    if result.get("video_path"):
        print(f"  Video: {result['video_path']}")
    if result.get("illustration_paths"):
        print(f"  Illustrations: {len(result['illustration_paths'])}")

    # Copy artifacts to web/static/sample/
    audio_dst = OUT_DIR / "sample.mp3"
    shutil.copy(result["file_path"], audio_dst)
    print(f"  → {audio_dst}")
    if result.get("video_path"):
        video_dst = OUT_DIR / "sample.mp4"
        shutil.copy(result["video_path"], video_dst)
        print(f"  → {video_dst}")
    if result.get("illustration_paths"):
        for i, p in enumerate(result["illustration_paths"]):
            dst = OUT_DIR / f"scene_{i+1}.png"
            shutil.copy(p, dst)
            print(f"  → {dst}")
    # Save text + title as JSON for the /sample page
    import json
    (OUT_DIR / "meta.json").write_text(json.dumps({
        "title": result["title"],
        "duration": result["duration"],
        "context": DEMO_CONTEXT,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
