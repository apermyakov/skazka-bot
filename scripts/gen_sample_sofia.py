"""Generate the demo story with София (synthetic 4yo, picked from girl_01)
as the hero, using the existing portrait as reference photo.

Cost: ~$0.25 (just the pipeline — portrait already exists).
"""
import asyncio
import base64
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, "/app")

REF_PORTRAIT = Path("/app/assets/kid_candidates/girl_01_blonde_curly.png")
OUT_DIR = Path("/app/web/static/sample")

CHILD_NAME = "София"
CHILD_AGE = 4

DEMO_CONTEXT = (
    f"Сказка про девочку {CHILD_NAME}, ей {CHILD_AGE} года. У Софии светлые кудряшки "
    "и голубые глаза. Вечером она не хочет ложиться спать одна — её немного "
    "пугает тишина и темнота в комнате. Однажды через щель в шторах к ней "
    "залетает маленький добрый светлячок по имени Лучик. Они вместе "
    "тихонько обходят комнату, разговаривают с любимым плюшевым мишкой, "
    "слушают, как дышит спящий кот, и София понимает: ночь — это не "
    "страшно, а уютно и тихо. Лучик остаётся охранять её сон. София "
    "засыпает с улыбкой."
)


async def main():
    if not REF_PORTRAIT.exists():
        print(f"FAIL: ref not found: {REF_PORTRAIT}"); sys.exit(1)
    png = REF_PORTRAIT.read_bytes()
    ref_b64 = base64.b64encode(png).decode("ascii")
    print(f"Reference: {REF_PORTRAIT.name} ({len(png)/1024:.0f} KB)")

    from engine.pipeline import generate_fairytale

    print("\n→ running full pipeline (~10 min)…")
    async def progress(msg):
        safe = msg.encode("ascii", "replace").decode("ascii")
        print(f"  [{safe}]")

    result = await generate_fairytale(
        context=DEMO_CONTEXT,
        reference_photo_b64=ref_b64,
        on_status=progress,
    )

    print(f"\nDONE.")
    print(f"  Title: {result['title']}")
    print(f"  Duration: {result['duration']:.1f}s")
    print(f"  Audio: {result['file_path']}")
    if result.get("video_path"):
        print(f"  Video: {result['video_path']}")
    if result.get("illustration_paths"):
        print(f"  Illustrations: {len(result['illustration_paths'])}")

    # Clean old, copy new
    for old in OUT_DIR.glob("scene_*"):
        old.unlink()
    for old in OUT_DIR.glob("sample.*"):
        old.unlink()

    shutil.copy(result["file_path"], OUT_DIR / "sample.mp3")
    print(f"  → sample.mp3")
    if result.get("video_path"):
        shutil.copy(result["video_path"], OUT_DIR / "sample.mp4")
        print(f"  → sample.mp4")
    if result.get("illustration_paths"):
        for i, p in enumerate(result["illustration_paths"]):
            shutil.copy(p, OUT_DIR / f"scene_{i+1}.png")
            print(f"  → scene_{i+1}.png")

    (OUT_DIR / "meta.json").write_text(json.dumps({
        "title": result["title"],
        "duration": result["duration"],
        "context": DEMO_CONTEXT,
        "child": {"name": CHILD_NAME, "age": CHILD_AGE, "synthetic": True,
                  "portrait": "girl_01_blonde_curly"},
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
