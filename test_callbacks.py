# -*- coding: utf-8 -*-
"""Quick E2E test: verify callback order (audio before illustrations)."""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


async def main():
    from engine.pipeline import generate_fairytale

    context = (
        "Tema skazki: Pro kotenka Mursa, kotoryj bojalsya temnoty\n"
        "Informaciya o rebyonke: Masha, 5 let, lyubit koshek"
    )

    events = []  # (timestamp, event_type, detail)
    t0 = time.time()

    def elapsed():
        return f"{time.time() - t0:.1f}s"

    async def on_status(msg):
        safe = msg.encode("ascii", "replace").decode("ascii")
        events.append((elapsed(), "status", safe))
        print(f"  [{elapsed()}] STATUS: {safe}")

    async def on_audio_ready(info):
        events.append((elapsed(), "audio_ready", info["title"]))
        print(f"  [{elapsed()}] AUDIO READY: {info['title']}, {info['duration']:.1f}s, file={info['file_path']}")

    async def on_illustration_ready(idx, path, style):
        events.append((elapsed(), "illustration", f"scene_{idx + 1}_{style}"))
        print(f"  [{elapsed()}] ILLUSTRATION: scene {idx + 1} [{style}] -> {path}")

    print("=" * 60)
    print("CALLBACK ORDER TEST")
    print("=" * 60)
    print(f"  Context: {context[:80]}...")
    print()

    try:
        result = await generate_fairytale(
            context=context,
            reference_photo_b64=None,
            on_status=on_status,
            on_audio_ready=on_audio_ready,
            on_illustration_ready=on_illustration_ready,
        )
    except Exception as e:
        print(f"\n  PIPELINE FAILED: {e}")
        import traceback
        traceback.print_exc()
        return

    print(f"\n{'=' * 60}")
    print("RESULTS")
    print(f"{'=' * 60}")

    title = result["title"]
    duration = result["duration"]
    illustrations = result.get("illustrations", [])
    video_path = result.get("video_path")

    print(f"  Title:          {title}")
    print(f"  Duration:       {duration:.1f}s")
    print(f"  MP3:            {result['file_path']}")
    print(f"  Illustrations:  {len(illustrations)}")
    print(f"  Video:          {video_path or 'none'}")

    # Validate callback order
    print(f"\n{'=' * 60}")
    print("CALLBACK ORDER VALIDATION")
    print(f"{'=' * 60}")

    audio_idx = None
    first_illustration_idx = None
    for i, (ts, etype, detail) in enumerate(events):
        if etype == "audio_ready":
            audio_idx = i
        if etype == "illustration" and first_illustration_idx is None:
            first_illustration_idx = i

    checks = []

    # 1. Audio callback fired
    checks.append(("on_audio_ready fired", audio_idx is not None))

    # 2. Illustrations fired
    illustration_events = [(i, e) for i, e in enumerate(events) if e[1] == "illustration"]
    checks.append(("Illustrations generated", len(illustration_events) > 0))

    # 3. Audio BEFORE illustrations
    if audio_idx is not None and first_illustration_idx is not None:
        checks.append(("Audio sent BEFORE illustrations", audio_idx < first_illustration_idx))
    else:
        checks.append(("Audio sent BEFORE illustrations", False))

    # 4. Both styles present
    styles = set(e[2].split("_")[-1] for _, e in illustration_events)
    checks.append(("Pixar style present", "pixar" in styles))
    checks.append(("Watercolor style present", "watercolor" in styles))

    # 5. Paired: same number of pixar and watercolor
    pixar_count = sum(1 for _, e in illustration_events if "pixar" in e[2])
    watercolor_count = sum(1 for _, e in illustration_events if "watercolor" in e[2])
    checks.append((f"Pixar count ({pixar_count}) == Watercolor count ({watercolor_count})", pixar_count == watercolor_count))

    # 6. File checks
    checks.append(("MP3 exists", os.path.exists(result["file_path"])))
    checks.append(("MP3 size > 100KB", os.path.getsize(result["file_path"]) > 100_000))
    checks.append(("Duration > 60s", duration > 60))

    # 7. Illustration files exist
    all_illust_exist = all(os.path.exists(p) for p in illustrations)
    checks.append((f"All illustration files exist ({len(illustrations)})", all_illust_exist))

    # 8. Video
    if video_path:
        checks.append(("Video exists", os.path.exists(video_path)))

    print()
    all_ok = True
    for name, ok in checks:
        status = "PASS" if ok else "FAIL"
        if not ok:
            all_ok = False
        print(f"  [{status}] {name}")

    print(f"\n  EVENT LOG ({len(events)} events):")
    for ts, etype, detail in events:
        print(f"    {ts:>7} | {etype:<15} | {detail}")

    print(f"\n  {'ALL CHECKS PASSED' if all_ok else 'SOME CHECKS FAILED'}")
    print(f"  Total time: {elapsed()}")


if __name__ == "__main__":
    asyncio.run(main())
