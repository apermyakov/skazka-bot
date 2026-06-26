"""Regenerate illustrations for an existing paid order using the NEW
image-generation pipeline (VLM photo analysis + topic-aware scene_split +
photo-priority image prompt). Re-uses the order's existing screenplay (from
api_calls) so we don't burn TTS credits — only image gen + 1 small VLM call.

Run:
  docker exec skazka-bot python /app/scripts/regen_full_pipeline.py <order_id>

Backs up original illustrations to illustrations_backup_<timestamp>/ first,
then rebuilds the video with the new images.
"""
from __future__ import annotations
import argparse
import asyncio
import base64
import json
import logging
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, "/app")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("regen-full")


async def build_screenplay(story_text: str, title: str) -> dict:
    """Regenerate screenplay from intact story_text (api_calls response_text
    is truncated to 10000 chars, so cannot be reused for long stories).

    convert_to_screenplay raises ValueError on missing required fields and does
    NOT retry — wrap with our own 5-attempt loop for fragile Gemini outputs.
    """
    from engine.llm_client import convert_to_screenplay
    last_err = None
    for attempt in range(1, 6):
        try:
            sp = await convert_to_screenplay(title=title, text=story_text)
            log.info("screenplay convert ok on attempt %d", attempt)
            return sp
        except (ValueError, Exception) as e:
            last_err = e
            log.warning("screenplay convert attempt %d failed: %s", attempt, e)
    raise RuntimeError(f"screenplay convert failed 5x, last={last_err}")


async def main(order_id: str) -> None:
    import db.database as db_mod
    from db.config_manager import cfg
    if db_mod._pool is None:
        await db_mod.init_db()
    cfg.set_pool(db_mod._pool)
    async with db_mod._pool.acquire() as c:
        order = await c.fetchrow("SELECT * FROM web_orders WHERE id=$1", order_id)
    if not order:
        log.error("order %s not found", order_id); return

    title = order["title"]
    topic = order["topic"]
    photo_path = order["photo_path"]
    mid = order["media_order_id"]
    media_dir = Path("/app/media") / mid
    illust_dir = media_dir / "illustrations"
    audio_path = media_dir / "final.mp3"
    video_path = media_dir / "fairytale.mp4"

    log.info("order %s / title=%r", order_id, title)
    log.info("topic[:200]=%r", (topic or "")[:200])
    log.info("photo=%s", photo_path)
    log.info("media_dir=%s", media_dir)

    if not illust_dir.exists():
        log.error("illustrations dir missing"); return

    # Backup originals
    backup = media_dir / f"illustrations_backup_{int(time.time())}"
    shutil.copytree(illust_dir, backup)
    log.info("backed up to %s", backup)

    story_text = order["story_text"]
    if not story_text:
        log.error("no story_text for order"); return
    log.info("regenerating screenplay from story_text (%d chars)…", len(story_text))
    screenplay = await build_screenplay(story_text, title)
    log.info("screenplay: %r, %d characters, %d segments",
             screenplay.get("title"),
             len(screenplay.get("characters", [])),
             len(screenplay.get("segments", [])))

    photo_b64 = None
    if photo_path and Path(photo_path).exists():
        photo_b64 = base64.b64encode(Path(photo_path).read_bytes()).decode("ascii")
        log.info("photo loaded, %d bytes", len(photo_b64))

    from engine.image_generator import generate_illustrations_batch
    log.info("starting illustration generation with NEW pipeline (VLM + topic + photo-priority)…")
    images, scenes = await generate_illustrations_batch(
        screenplay=screenplay,
        reference_photo_b64=photo_b64,
        story_id=None,
        timeline_text=None,
        style="painted",
        topic=topic,
    )
    log.info("got %d illustrations (%d non-empty)",
             len(images), sum(1 for i in images if i))

    # Write new illustrations
    for i, img in enumerate(images, 1):
        if img:
            out = illust_dir / f"scene_{i}.png"
            out.write_bytes(img)
            log.info("  wrote %s (%d KB)", out, len(img) // 1024)

    # Rebuild video
    log.info("rebuilding video…")
    from engine.audio_mixer import create_video, get_duration
    total_scenes = len(images)
    illust_paths = [str(illust_dir / f"scene_{i}.png")
                     for i in range(1, total_scenes + 1)
                     if (illust_dir / f"scene_{i}.png").exists()]
    total_dur = await get_duration(str(audio_path))
    per_scene = total_dur / max(1, len(illust_paths))
    durations = [per_scene] * len(illust_paths)
    await create_video(str(audio_path), illust_paths, str(video_path), durations=durations)
    log.info("video rebuilt: %s (%d MB)", video_path,
             video_path.stat().st_size // (1024 * 1024))
    log.info("URL: https://skazik.app/media/%s/fairytale.mp4", mid)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("order_id")
    args = ap.parse_args()
    asyncio.run(main(args.order_id))
