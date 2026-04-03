# -*- coding: utf-8 -*-
"""End-to-end test: generate 3 fairy tales and validate results."""

import asyncio
import sys
import os
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TOPICS = [
    {
        "context": (
            "Тема сказки: Про храброго котёнка Мурзика, который боялся темноты\n"
            "Информация о ребёнке: Маша, 5 лет, любит кошек"
        ),
        "label": "Fairy tale #1: Brave kitten",
    },
    {
        "context": (
            "Тема сказки: Как маленький робот научился дружить\n"
            "Информация о ребёнке: Артём, 7 лет, любит роботов и конструкторы"
        ),
        "label": "Fairy tale #2: Little robot",
    },
    {
        "context": (
            "Тема сказки: Про дракона, который вместо огня выдыхал мыльные пузыри\n"
            "Информация о ребёнке: Соня, 4 года, любит мыльные пузыри"
        ),
        "label": "Fairy tale #3: Bubble dragon",
    },
]


def get_duration(filepath):
    res = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", filepath],
        capture_output=True, text=True,
    )
    return float(res.stdout.strip()) if res.returncode == 0 else 0


async def run_test(topic_info, index):
    from engine.pipeline import generate_fairytale

    label = topic_info["label"]
    context = topic_info["context"]

    print(f"\n{'='*60}")
    print(f"TEST {index}: {label}")
    print(f"{'='*60}")

    steps = []

    async def progress(msg):
        safe = msg.encode("ascii", "replace").decode("ascii")
        steps.append(safe)
        print(f"  [{index}] {safe}")

    try:
        result = await generate_fairytale(context=context, on_progress=progress)

        filepath = result["file_path"]
        duration = result["duration"]
        segments = result["segments_count"]
        title = result["title"]
        file_size = os.path.getsize(filepath)

        # Validate
        checks = []
        checks.append(("File exists", os.path.exists(filepath)))
        checks.append(("File size > 100KB", file_size > 100_000))
        checks.append(("Duration > 60s", duration > 60))
        checks.append(("Duration < 600s", duration < 600))
        checks.append(("Segments >= 10", segments >= 10))
        checks.append(("Title not empty", len(title) > 0))
        checks.append(("Progress steps >= 5", len(steps) >= 5))

        all_ok = all(v for _, v in checks)

        print(f"\n  RESULT:")
        print(f"  Title:    {title.encode('ascii','replace').decode()}")
        print(f"  File:     {filepath}")
        print(f"  Size:     {file_size:,} bytes")
        print(f"  Duration: {duration:.1f}s ({duration/60:.1f} min)")
        print(f"  Segments: {segments}")
        print(f"  Steps:    {len(steps)}")
        print(f"\n  CHECKS:")
        for name, ok in checks:
            status = "PASS" if ok else "FAIL"
            print(f"    [{status}] {name}")

        print(f"\n  {'PASSED' if all_ok else 'FAILED'}")
        return all_ok

    except Exception as e:
        print(f"\n  EXCEPTION: {e}")
        return False


async def main():
    print("=" * 60)
    print("SKAZKA BOT - END TO END TEST")
    print("Testing full pipeline: LLM -> TTS -> Mix -> MP3")
    print("=" * 60)

    results = []
    for i, topic in enumerate(TOPICS, 1):
        ok = await run_test(topic, i)
        results.append((topic["label"], ok))

    print(f"\n\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    passed = sum(1 for _, ok in results if ok)
    for label, ok in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    print(f"\n  {passed}/{len(results)} passed")

    if passed == len(results):
        print("\n  ALL TESTS PASSED - BOT IS READY FOR USER TESTING!")
    else:
        print("\n  SOME TESTS FAILED - NEEDS FIXING")


if __name__ == "__main__":
    asyncio.run(main())
