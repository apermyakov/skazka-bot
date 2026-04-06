# -*- coding: utf-8 -*-
"""Test ONLY illustration generation — no LLM, no TTS, no ElevenLabs.

Calls generate_illustration() directly with hardcoded scenes,
bypassing split_into_scenes() entirely (zero LLM calls).

Usage:
    python test_illustrations.py              # without reference photo
    python test_illustrations.py photo.jpg    # with reference photo
"""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TITLE = "Котёнок Мурзик и звёздный фонарик"
CHARACTERS_DESC = "Мурзик (храбрый котёнок), Сова (мудрая наставница)"

CHARACTER_APPEARANCES = {
    "Маша": "Девочка 5 лет, русые волосы с двумя хвостиками, голубые глаза, розовое платье",
    "Мурзик": "Маленький серый котёнок с зелёными глазами и белым пятном на груди",
    "Сова": "Большая мудрая сова с коричневыми перьями и очками на носу",
}

TEST_SCENES = [
    {
        "description": "Маша и Мурзик сидят у окна, за окном темнота",
        "characters_present": ["Маша", "Мурзик"],
        "setting": "детская комната",
        "mood": "таинственный",
    },
    {
        "description": "Мурзик находит светящийся фонарик на чердаке",
        "characters_present": ["Мурзик"],
        "setting": "чердак",
        "mood": "волшебный",
    },
    {
        "description": "Маша и Мурзик гуляют по ночному лесу с фонариком",
        "characters_present": ["Маша", "Мурзик", "Сова"],
        "setting": "ночной лес",
        "mood": "волшебный",
    },
]


async def main():
    from engine.image_generator import generate_illustration

    photo_b64 = None
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        import base64
        with open(sys.argv[1], "rb") as f:
            photo_b64 = base64.b64encode(f.read()).decode("ascii")
        print(f"Using reference photo: {sys.argv[1]}")

    t0 = time.time()
    total = len(TEST_SCENES)
    successful = 0
    prev_desc = None

    print("=" * 50)
    print(f"ILLUSTRATION-ONLY TEST ({total} scenes)")
    print(f"Model: {__import__('engine.image_generator', fromlist=['IMAGE_MODEL']).IMAGE_MODEL}")
    print("=" * 50)

    for i, scene in enumerate(TEST_SCENES):
        elapsed = time.time() - t0
        print(f"\n  [{elapsed:.1f}s] Generating scene {i + 1}/{total}...")

        img_bytes = await generate_illustration(
            scene=scene,
            scene_index=i,
            total_scenes=total,
            reference_photo_b64=photo_b64,
            previous_scene_desc=prev_desc,
            fairy_tale_title=TITLE,
            characters_desc=CHARACTERS_DESC,
            character_appearances=CHARACTER_APPEARANCES,
        )

        elapsed = time.time() - t0
        if img_bytes:
            out_path = f"test_scene_{i + 1}.png"
            with open(out_path, "wb") as f:
                f.write(img_bytes)
            print(f"  [{elapsed:.1f}s] Scene {i + 1}: {len(img_bytes):,}b -> {out_path}")
            successful += 1
        else:
            print(f"  [{elapsed:.1f}s] Scene {i + 1}: FAILED")

        prev_desc = scene.get("description", "")

    elapsed = time.time() - t0
    print(f"\nResults: {successful}/{total} illustrations in {elapsed:.1f}s")
    print("OK" if successful > 0 else "FAILED")
    sys.exit(0 if successful > 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
