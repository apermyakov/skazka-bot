#!/usr/bin/env python3
"""Regenerate specific illustrations + rebuild video for a paid skazik order.

Skips the screenplay step entirely — that LLM step is flaky and not worth
re-running when we only need 1-2 scenes fixed. Instead, scene descriptions
are read from a small JSON file the operator hand-crafts based on the story
and the broken scene's visual issue.

JSON format (one entry per scene to regenerate, indices are 1-based):
{
  "main_character": "Diana",
  "main_appearance": "small 4-year-old girl, soft brown shoulder-length hair, ...",
  "scenes": {
    "1": {
      "setting": "cozy bedroom at night",
      "mood": "warm, peaceful",
      "description": "Diana lying in her bed with carrot-pattern blanket...",
      "characters_present": ["Diana"]
    },
    "11": { ... }
  }
}

Run from container:
  docker exec skazka-bot python /app/scripts/regen_order_illustrations.py \
    --order 65ed28135af14e7e --config /app/scripts/diana_fix.json

Pass --dry-run to skip the actual API calls.
"""
from __future__ import annotations
import argparse, asyncio, base64, json, logging, sys
from pathlib import Path

sys.path.insert(0, "/app")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("regen-illust")


async def get_order(oid: str) -> dict | None:
    import db.database as dbmod
    if dbmod._pool is None:
        from db.database import init_db
        await init_db()
    async with dbmod._pool.acquire() as c:
        r = await c.fetchrow("SELECT * FROM web_orders WHERE id=$1", oid)
    return dict(r) if r else None


async def regen(oid: str, cfg_path: Path, dry_run: bool = False) -> None:
    order = await get_order(oid)
    if not order:
        log.error("order %s not found", oid); return
    if order["status"] != "done":
        log.error("order %s status=%s — only done orders can be patched", oid, order["status"])
        return

    mid = order["media_order_id"]
    media_dir = Path("/app/media") / mid
    illust_dir = media_dir / "illustrations"
    audio_path = media_dir / "final.mp3"
    video_path = media_dir / "fairytale.mp4"
    for p, name in [(media_dir,"media"),(illust_dir,"illust"),(audio_path,"audio"),(video_path,"video")]:
        if not p.exists():
            log.error("missing %s at %s", name, p); return

    existing = sorted(illust_dir.glob("scene_*.png"),
                       key=lambda p: int(p.stem.split("_")[1]))
    total_scenes = len(existing)

    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    requested = cfg.get("scenes") or {}
    if not requested:
        log.error("config has no scenes to regenerate"); return

    log.info("found %d existing illustrations, regenerating: %s",
             total_scenes, list(requested.keys()))

    reference_b64 = None
    if order.get("photo_path"):
        photo = Path(order["photo_path"])
        if photo.exists():
            reference_b64 = base64.b64encode(photo.read_bytes()).decode("ascii")
            log.info("using reference photo: %s", photo)

    main_name = cfg.get("main_character", "Hero")
    main_appearance = cfg.get("main_appearance", "")
    extra_characters = cfg.get("extra_characters") or {}
    character_appearances = {main_name: main_appearance} if main_appearance else {}
    character_appearances.update(extra_characters)
    characters_desc = ", ".join([main_name, *extra_characters.keys()])

    from engine.image_generator import generate_illustration, _resolve_style_block
    style_block = await _resolve_style_block(None)
    title = order["title"] or "Сказка"

    for idx_str, scene in requested.items():
        idx = int(idx_str)
        if not (1 <= idx <= total_scenes):
            log.error("scene %d out of range 1..%d", idx, total_scenes); continue
        log.info("scene %d: %s", idx, (scene.get("description") or "")[:100])
        if dry_run:
            log.info("dry-run, skipping API call"); continue
        try:
            img_bytes = await generate_illustration(
                scene=scene,
                scene_index=idx - 1,
                total_scenes=total_scenes,
                reference_photo_b64=reference_b64,
                previous_scene_desc=None,
                fairy_tale_title=title,
                characters_desc=characters_desc,
                character_appearances=character_appearances,
                style_block=style_block,
            )
        except Exception as e:
            log.error("scene %d generation failed: %s", idx, e); continue
        if not img_bytes:
            log.error("scene %d returned no bytes", idx); continue
        out = illust_dir / f"scene_{idx}.png"
        out.write_bytes(img_bytes)
        log.info("  → wrote %s (%d KB)", out, len(img_bytes) // 1024)

    if dry_run:
        log.info("dry-run done, video not rebuilt"); return

    log.info("rebuilding video…")
    from engine.audio_mixer import create_video, get_duration
    illust_paths = [str(illust_dir / f"scene_{i}.png") for i in range(1, total_scenes + 1)]
    total_dur = await get_duration(str(audio_path))
    per_scene = total_dur / total_scenes
    durations = [per_scene] * total_scenes
    await create_video(str(audio_path), illust_paths, str(video_path), durations=durations)
    log.info("video rebuilt: %s (%d MB)", video_path,
             video_path.stat().st_size // (1024 * 1024))
    log.info("public URL: https://skazik.app/media/%s/fairytale.mp4", mid)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--order", required=True, help="web_orders.id")
    ap.add_argument("--config", required=True, help="path to JSON config with scenes to regen")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    asyncio.run(regen(args.order, Path(args.config), dry_run=args.dry_run))


if __name__ == "__main__":
    main()
