#!/usr/bin/env python3
"""v3 demos — painted style with the SAME child from the photo throughout.

For each locale:
  1. Load /static/lalaka_examples/{locale}_photo.png as reference
  2. Generate 3 painted-storybook scenes passing that photo as image_url:
     - Scene 1: child tucked in bed (introduction)
     - Scene 2: magical helper arrives (locale-specific folklore figure)
     - Scene 3: child sleeping peacefully with magical glow
  3. Reuse cached TTS audio from .lalaka_story_work_v2/{locale}.mp3
  4. ffmpeg slideshow

Output: /app/web/static/lalaka_demos/{locale}.mp4
Cost: ~33 illustrations × $0.04 = $1.32, ~15 min sequential.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, "/app")

REPO = Path("/app")
STATIC = REPO / "web" / "static" / "lalaka_demos"
EXAMPLES = REPO / "web" / "static" / "lalaka_examples"
WORK = REPO / ".lalaka_story_work_v3"
AUDIO_CACHE = REPO / ".lalaka_story_work_v2"  # reuse TTS from v2
STATIC.mkdir(parents=True, exist_ok=True)
WORK.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("story-demo-v3")

LOCALES = ["en", "de", "es", "fr", "it", "pl", "pt-BR", "tr", "ja", "ko", "ar"]

# Folklore helper figure per locale (referenced in scene 2)
HELPERS = {
    "en":    "a tiny silver fairy-star with delicate wings",
    "de":    "the Sandmännchen (Sand-man) sprinkling golden sleep-sand from a small pouch",
    "es":    "a tiny silvery moon-fairy (Luna)",
    "fr":    "a glowing silver firefly (luciole)",
    "it":    "a tiny golden firefly (lucciola)",
    "pl":    "a small glowing guardian angel (Anioł Stróż) silhouette",
    "pt-BR": "a tiny magical golden firefly (vaga-lume mágico)",
    "tr":    "a small silver crescent-shaped fairy",
    "ja":    "a tiny glowing silver-gold star spirit with cherry blossom petals",
    "ko":    "a small star friend with soft golden light",
    "ar":    "a tiny silver star descended from the crescent Hilal moon",
}

# Cultural bedroom hint per locale (same as v2)
BEDROOM_HINTS = {
    "en":    "a cosy Western-style children's bedroom with a round moon window, wooden bedside table and mushroom-shaped night lamp",
    "de":    "a cosy traditional Bavarian/Alpine children's bedroom with wooden beams, wooden bedside table and a small wooden window showing the night sky",
    "es":    "a cosy Mediterranean Spanish children's bedroom with whitewashed walls, terracotta-tiled floor and an arched window showing the warm night sky",
    "fr":    "a cosy French children's bedroom with mansard ceiling and a small Parisian-style window showing a starry night sky and rooftops",
    "it":    "a cosy Italian children's bedroom with terracotta-tiled floor, painted wooden bedframe and an open shutter window showing the warm night sky",
    "pl":    "a cosy traditional Polish children's bedroom with whitewashed walls, dark wood furniture and folk-art decorative motifs",
    "pt-BR": "a cosy Brazilian children's bedroom with bright tropical accents, wooden ceiling fan and a window showing a warm tropical night sky with palm fronds",
    "tr":    "a cosy traditional Turkish children's bedroom with a hand-woven Anatolian kilim rug, mashrabiya-style carved wooden window showing the crescent moon",
    "ja":    "a cosy Japanese-style children's room (washitsu) with tatami mat floor, a small futon on the floor, a paper-lantern (chōchin) warm light and round shoji-style window with cherry blossom branch",
    "ko":    "a cosy modern Korean children's bedroom with ondol-warm wooden floor, a low bed and a ceramic moon-jar style night lamp",
    "ar":    "a cosy traditional Middle-Eastern children's bedroom with intricate mashrabiya wooden lattice window, a Moroccan-style metal lantern (fanous) casting warm golden patterned light",
}

# Painted style — matches skazik's prompt.style.painted exactly
PAINTED_STYLE = (
    "CLASSIC HAND-PAINTED FAIRY-TALE STORYBOOK ILLUSTRATION style — rich gouache and oil "
    "painting with visible brushwork, warm golden light, painterly textures, the timeless "
    "look of a treasured children's picture book. Painterly, NOT flat vector, NOT photographic, "
    "NOT Pixar 3D, NOT digital cartoon. Think classic European children's-book illustration."
)

FACE_LOCK = (
    "The child's face must be RECOGNISABLY the SAME child from the reference photo — "
    "same hair colour/style, same eye colour, same skin tone, same facial features (nose, "
    "mouth, smile), same age. Preserve identity from the photo."
)

# Three narrative scenes per locale
def make_scene_prompt(locale: str, scene_idx: int) -> str:
    helper = HELPERS[locale]
    bedroom = BEDROOM_HINTS[locale]
    if scene_idx == 0:
        action = (
            f"The child is just being tucked into bed at bedtime in {bedroom}. "
            "She is sitting up under a soft purple-pink duvet, smiling gently. "
            "The bedside lamp glows softly. Through the window: starry night with a "
            "growing crescent moon. Warm cosy mood."
        )
    elif scene_idx == 1:
        action = (
            f"Same bedroom: {bedroom}. The child has settled back, eyes wide with wonder, "
            f"as {helper} has just appeared near her pillow, glowing softly. Magical golden "
            "sparkles fill the air. The child looks at the helper with gentle surprise and joy."
        )
    else:
        action = (
            f"Same bedroom: {bedroom}. The child is now sleeping peacefully, her face "
            "softly visible against the pillow with a contented smile. The {helper} rests "
            f"on the pillow next to her as a gentle nightlight. Soft music notes drift in the "
            "air. Through the window: peaceful starry night. Dreamy late-night light."
        )
        action = action.format(helper=helper.replace("a ", "the ").replace("the the ", "the "))

    return (
        f"{PAINTED_STYLE}\n\n"
        f"{FACE_LOCK}\n\n"
        f"Scene: {action}\n\n"
        "Warm purple-pink palette, soft golden light. Single child only. "
        "STRICT: classic painted-storybook style with visible brushwork, NOT Pixar 3D. "
        "NO text, NO captions. 16:9 cinematic composition."
    )


async def gen_scene(locale: str, idx: int, photo_b64: str, sem: asyncio.Semaphore) -> Path | None:
    out_path = WORK / f"{locale}_{idx}.png"
    if out_path.exists():
        logger.info(f"  [{locale}] scene{idx}: cached")
        return out_path
    from engine.image_generator import _call_image_api
    prompt = make_scene_prompt(locale, idx)
    content = [
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{photo_b64}"}},
        {"type": "text", "text": prompt},
    ]
    async with sem:
        try:
            data = await _call_image_api(
                content=content,
                scene_index=idx,
                style_label="painted",
                story_id=None,
            )
            if not data:
                logger.warning(f"  [{locale}] scene{idx}: no data")
                return None
            out_path.write_bytes(data)
            logger.info(f"  [{locale}] scene{idx}: ✓ ({len(data)}B)")
            return out_path
        except Exception as e:
            logger.error(f"  [{locale}] scene{idx}: {e}")
            return None


async def make_video(locale: str, scenes: list[Path], audio: Path, out_path: Path):
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(audio),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    dur = float(stdout.decode().strip() or "25")
    n = len(scenes)
    per_scene = dur / n
    fade = 0.8
    fade_out_start = max(0, dur - fade)
    inputs = []
    for s in scenes:
        inputs.extend(["-loop", "1", "-t", f"{per_scene:.3f}", "-i", str(s)])
    fparts = []
    for i in range(n):
        fparts.append(
            f"[{i}:v]scale=2400:-2,zoompan=z='min(zoom+0.0006,1.06)':"
            f"d={int(per_scene*24)}:s=1920x1080:fps=24[v{i}]"
        )
    chain = "[v0]"
    t_cur = per_scene
    for i in range(1, n):
        off = t_cur - 0.6
        fparts.append(f"{chain}[v{i}]xfade=transition=fade:duration=0.6:offset={off:.3f}[xf{i}]")
        chain = f"[xf{i}]"
        t_cur += per_scene - 0.6
    fparts.append(f"{chain}fade=t=in:st=0:d={fade},fade=t=out:st={fade_out_start}:d={fade}[vout]")
    af = f"afade=t=in:st=0:d={fade},afade=t=out:st={fade_out_start}:d={fade}"
    fparts.append(f"[{n}:a]{af}[aout]")
    fc = ";".join(fparts)
    cmd = [
        "ffmpeg", "-y", *inputs, "-i", str(audio),
        "-filter_complex", fc,
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "22", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart",
        str(out_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg fail: {stderr.decode()[-500:]}")
    logger.info(f"[{locale}] MP4 ✓ {out_path.stat().st_size}B duration={dur:.1f}s")


async def run_locale(locale: str, force: bool):
    out_mp4 = STATIC / f"{locale}.mp4"
    if out_mp4.exists() and not force:
        logger.info(f"[{locale}] skip (exists)")
        return
    photo_path = EXAMPLES / f"{locale}_photo.png"
    if not photo_path.exists():
        logger.error(f"[{locale}] no photo at {photo_path}")
        return
    audio_path = AUDIO_CACHE / f"{locale}.mp3"
    if not audio_path.exists():
        logger.error(f"[{locale}] no cached TTS at {audio_path}")
        return
    logger.info(f"[{locale}] starting…")
    photo_b64 = base64.b64encode(photo_path.read_bytes()).decode("ascii")
    sem = asyncio.Semaphore(3)
    scenes_results = await asyncio.gather(
        gen_scene(locale, 0, photo_b64, sem),
        gen_scene(locale, 1, photo_b64, sem),
        gen_scene(locale, 2, photo_b64, sem),
    )
    scenes = [s for s in scenes_results if s is not None]
    if len(scenes) != 3:
        logger.error(f"[{locale}] only {len(scenes)}/3 scenes ready")
        return
    await make_video(locale, scenes, audio_path, out_mp4)


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--locale")
    p.add_argument("--force", action="store_true")
    args = p.parse_args()
    targets = [args.locale] if args.locale else LOCALES
    logger.info(f"v3 targets: {targets}")
    for loc in targets:
        try:
            await run_locale(loc, args.force)
        except Exception as e:
            logger.error(f"[{loc}] FAILED: {e}", exc_info=True)
    logger.info(f"done")


if __name__ == "__main__":
    asyncio.run(main())
