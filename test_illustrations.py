# -*- coding: utf-8 -*-
"""Test ONLY illustration generation — no LLM, no TTS, no ElevenLabs."""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Hardcoded screenplay — no need to call LLM
TEST_SCREENPLAY = {
    "title": "Котёнок Мурзик и звёздный фонарик",
    "characters": [
        {"id": "narrator", "name": "Рассказчик", "gender": "female", "age": "middle", "role": "narrator", "personality": "тёплый и добрый"},
        {"id": "murzik", "name": "Мурзик", "gender": "male", "age": "child", "role": "main", "personality": "храбрый котёнок"},
        {"id": "owl", "name": "Сова", "gender": "female", "age": "elderly", "role": "mentor", "personality": "мудрая"},
    ],
    "segments": [
        {"character_id": "narrator", "text": "Жил-был котёнок Мурзик.", "emotion": "neutral", "pace": "slow"},
        {"character_id": "murzik", "text": "Мне страшно в темноте!", "emotion": "scared", "pace": "normal"},
    ],
    "scenes": [],
}


async def main():
    from engine.image_generator import generate_illustrations_batch

    # Optional: pass a reference photo
    photo_b64 = None
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        import base64
        with open(sys.argv[1], "rb") as f:
            photo_b64 = base64.b64encode(f.read()).decode("ascii")
        print(f"Using reference photo: {sys.argv[1]}")

    t0 = time.time()

    async def on_progress(msg):
        elapsed = time.time() - t0
        print(f"  [{elapsed:.1f}s] {msg}")

    async def on_illustration_ready(idx, img_bytes):
        elapsed = time.time() - t0
        out_path = f"test_scene_{idx + 1}.png"
        with open(out_path, "wb") as f:
            f.write(img_bytes)
        print(f"  [{elapsed:.1f}s] Scene {idx + 1}: {len(img_bytes):,}b -> {out_path}")

    print("=" * 50)
    print("ILLUSTRATION-ONLY TEST")
    print("=" * 50)

    results = await generate_illustrations_batch(
        screenplay=TEST_SCREENPLAY,
        reference_photo_b64=photo_b64,
        on_progress=on_progress,
        on_illustration_ready=on_illustration_ready,
    )

    successful = sum(1 for r in results if r is not None)
    elapsed = time.time() - t0

    print(f"\nResults: {successful}/{len(results)} illustrations")
    print(f"Time: {elapsed:.1f}s")

    if successful == 0:
        print("FAILED — no illustrations generated")
        sys.exit(1)
    else:
        print("OK")


if __name__ == "__main__":
    asyncio.run(main())
