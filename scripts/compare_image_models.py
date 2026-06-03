#!/usr/bin/env python3
"""A/B test: generate OpenAI illustrations using last N user-uploaded photos.

For each of the most recent N photos in /app/media/_photos/, generates one
illustration with `openai/gpt-5.4-image-2` using the same prompt that was
already used to make a Gemini illustration (default: scene 1 of story 77).

Output goes to /app/media/compare_<timestamp>/, served via nginx.
The existing Gemini reference illustration is copied alongside for visual A/B.
"""

import asyncio
import base64
import json
import os
import shutil
import sys
import time
from pathlib import Path

import aiohttp
import asyncpg

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENAI_MODEL = "openai/gpt-5.4-image-2"
PHOTOS_DIR = Path("/app/media/_photos")
ILLUSTRATIONS_ROOT = Path("/app/media")


def load_env(path="/app/.env"):
    env = {}
    if Path(path).exists():
        for line in Path(path).read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    env.update(os.environ)
    return env


async def fetch_prompt(dsn: str, story_id: int, scene_idx: int = 1) -> tuple[str, str]:
    """Return (prompt_text, story_order_id) for the Nth illustration prompt of a story."""
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT request_text FROM api_calls "
            "WHERE story_id=$1 AND purpose='illustration' AND status='success' "
            "ORDER BY id LIMIT 10",
            story_id,
        )
        story = await conn.fetchrow("SELECT order_id FROM stories WHERE id=$1", story_id)
        if not rows or scene_idx > len(rows):
            return None, None
        return rows[scene_idx - 1]["request_text"], story["order_id"]
    finally:
        await conn.close()


async def gen_openai(session, photo_data_url: str, prompt: str, key: str):
    payload = {
        "model": OPENAI_MODEL,
        "modalities": ["image", "text"],
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": photo_data_url}},
                {"type": "text", "text": prompt},
            ],
        }],
        "image_config": {"aspect_ratio": "16:9", "image_size": "2K"},
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    t0 = time.time()
    async with session.post(OPENROUTER_URL, json=payload, headers=headers,
                            timeout=aiohttp.ClientTimeout(total=300)) as resp:
        body = await resp.text()
        dt = time.time() - t0
        if resp.status != 200:
            return None, f"HTTP {resp.status}: {body[:300]}", dt
        try:
            data = json.loads(body)
        except Exception as e:
            return None, f"json parse: {e}", dt
        if "error" in data:
            err = data["error"]
            err_str = err.get("message") if isinstance(err, dict) else str(err)
            return None, f"api error: {str(err_str)[:300]}", dt
        if "choices" not in data or not data["choices"]:
            return None, f"no choices in response: {body[:300]}", dt
        msg = data["choices"][0].get("message", {})
        refusal = msg.get("refusal")
        if refusal:
            return None, f"refusal: {str(refusal)[:200]}", dt
        images = msg.get("images") or []
        if not images:
            return None, "no images in response", dt
        url = images[0]
        if isinstance(url, dict):
            url = url.get("image_url", {}).get("url", "")
        if not url.startswith("data:"):
            return None, f"bad image format: {url[:80]}", dt
        b64 = url.split(",", 1)[1]
        return base64.b64decode(b64), "ok", dt


def latest_photos(n: int) -> list[Path]:
    files = sorted(PHOTOS_DIR.glob("*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:n]


async def main():
    story_id = int(sys.argv[1]) if len(sys.argv) > 1 else 77
    scene_idx = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    n_photos = int(sys.argv[3]) if len(sys.argv) > 3 else 5
    resume_dir = sys.argv[4] if len(sys.argv) > 4 else None  # optional: continue into existing dir

    env = load_env()
    or_key = env.get("OPENROUTER_API_KEY")
    if not or_key:
        sys.exit("ERROR: OPENROUTER_API_KEY not in env")
    dsn = env.get("DATABASE_URL", "").replace("postgresql://", "postgres://")

    prompt, order_id = await fetch_prompt(dsn, story_id, scene_idx)
    if not prompt:
        sys.exit(f"ERROR: no prompt for story {story_id} scene {scene_idx}")

    photos = latest_photos(n_photos)
    if not photos:
        sys.exit("ERROR: no photos in /app/media/_photos/")

    if resume_dir:
        out_dir = Path(resume_dir)
        ts = out_dir.name.replace("compare_", "")
        out_dir.mkdir(parents=True, exist_ok=True)
        print(f"Resuming into {out_dir}")
    else:
        ts = time.strftime("%Y%m%d-%H%M%S")
        out_dir = ILLUSTRATIONS_ROOT / f"compare_{ts}"
        out_dir.mkdir(parents=True, exist_ok=True)

    # Copy existing Gemini illustration (reference) if present
    gemini_src = ILLUSTRATIONS_ROOT / order_id / "illustrations" / f"scene_{scene_idx}.png"
    gemini_label = "no_gemini_reference_found"
    if gemini_src.exists():
        shutil.copy(gemini_src, out_dir / "gemini_reference.png")
        gemini_label = f"gemini_reference.png (from story {story_id} scene {scene_idx}, order {order_id})"

    # Save the prompt for review
    (out_dir / "prompt.txt").write_text(
        f"Source: story_id={story_id}, scene_idx={scene_idx}, order_id={order_id}\n\n{prompt}\n"
    )

    print(f"Story: {story_id} (order {order_id})")
    print(f"Scene: {scene_idx}")
    print(f"Prompt length: {len(prompt)} chars")
    print(f"Gemini ref: {gemini_label}")
    print(f"Photos to test ({len(photos)}):")
    for p in photos:
        print(f"  {p.name}  ({p.stat().st_size:,} bytes, mtime {time.strftime('%Y-%m-%d %H:%M', time.localtime(p.stat().st_mtime))})")
    print(f"Output: {out_dir}\n")

    # Copy input photos to output dir for easier inspection
    inputs_dir = out_dir / "inputs"
    inputs_dir.mkdir(exist_ok=True)
    for i, p in enumerate(photos, 1):
        shutil.copy(p, inputs_dir / f"photo_{i}_{p.name}")

    summary = []
    async with aiohttp.ClientSession() as sess:
        for i, photo_path in enumerate(photos, 1):
            out = out_dir / f"openai_photo_{i}.png"
            if out.exists():
                summary.append((i, photo_path.name, "skip-existing", 0, out.stat().st_size))
                print(f"[{i}/{len(photos)}] {photo_path.name} → SKIP (already exists, {out.stat().st_size:,}b)")
                continue
            print(f"[{i}/{len(photos)}] {photo_path.name}", end=" → ", flush=True)
            photo_b64 = base64.b64encode(photo_path.read_bytes()).decode()
            ext = photo_path.suffix.lower().lstrip(".")
            if ext == "jpg":
                ext = "jpeg"
            data_url = f"data:image/{ext};base64,{photo_b64}"

            img, status, dt = await gen_openai(sess, data_url, prompt, or_key)
            if img:
                out.write_bytes(img)
                summary.append((i, photo_path.name, "ok", dt, len(img)))
                print(f"OK {len(img):,}b ({dt:.1f}s)")
            else:
                summary.append((i, photo_path.name, status, dt, 0))
                print(f"FAIL [{status[:80]}] ({dt:.1f}s)")

    print(f"\n{'='*70}")
    print(f"Output: {out_dir}")
    print(f"{'#':<3}{'Photo':<40}{'Status':<25}{'Time':>8}{'Size':>10}")
    for i, name, status, dt, size in summary:
        print(f"{i:<3}{name[:39]:<40}{status[:24]:<25}{dt:>6.1f}s{size:>10,}")

    base_url = f"http://95.216.117.49/media/compare_{ts}"
    print(f"\nView in browser:")
    print(f"  Prompt:           {base_url}/prompt.txt")
    if gemini_src.exists():
        print(f"  Gemini reference: {base_url}/gemini_reference.png")
    print(f"  Inputs:           {base_url}/inputs/  (your uploaded photos)")
    for i in range(1, len(photos) + 1):
        out_path = out_dir / f"openai_photo_{i}.png"
        if out_path.exists():
            print(f"  OpenAI photo {i}:   {base_url}/openai_photo_{i}.png")


if __name__ == "__main__":
    asyncio.run(main())
