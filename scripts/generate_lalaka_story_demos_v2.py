#!/usr/bin/env python3
"""Culturally-adapted story demos for 11 Lalaka locales (v2).

Each locale gets its OWN master story leaning on local folklore + its OWN
3 illustrations with cultural scene hints (Japanese tatami, Arabic mashrabiya,
Brazilian tropical, etc.).

Output: /app/web/static/lalaka_demos/{locale}.mp4
Cost: ~$2 (33 illustrations × $0.04 + 11 TTS).
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/app")

import aiohttp

REPO = Path("/app")
STATIC = REPO / "web" / "static" / "lalaka_demos"
WORK = REPO / ".lalaka_story_work_v2"
STATIC.mkdir(parents=True, exist_ok=True)
WORK.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("story-demo-v2")

LOCALES = ["en", "de", "es", "fr", "it", "pl", "pt-BR", "tr", "ja", "ko", "ar"]

EL_LANG_CODE = {
    "en":"en","de":"de","es":"es","fr":"fr","it":"it","pl":"pl","pt-BR":"pt",
    "tr":"tr","ja":"ja","ko":"ko","ar":"ar",
}

CURATED_VOICES = {
    "en":    ("hpp4J3VqNfWAUOO0d1Us", "Bella"),
    "de":    ("dCnu06FiOZma2KVNUoPZ", "Mila Winter"),
    "es":    ("EXAVITQu4vr4xnSDxMaL", "Sarah"),
    "fr":    ("McVZB9hVxVSk3Equu8EH", "Audrey"),
    "it":    ("wJqPPQ618aTW29mptyoc", "Ana-Rita2"),
    "pl":    ("xsSg7GkDPDhaGZpbKOLn", "Tomasz Z"),
    "pt-BR": ("wJqPPQ618aTW29mptyoc", "Ana-Rita2"),
    "tr":    ("Sm1seazb4gs7RSlUVw7c", "Anika"),
    "ja":    ("3JDquces8E8bkmvbh6Bc", "Otani"),
    "ko":    ("uyVNoMrnUku1dZyVEXwD", "Anna Kim"),
    "ar":    ("IES4nrmZdUBHByLBde0P", "Haytham"),
}

# Per-locale master stories rooted in local folklore (~70-90 words each).
# Hand-crafted to feel idiomatic, not translated.
MASTER_STORIES = {
    "en": (
        "Lily was tucked into bed when the moon peeked through the curtains. A tiny silver "
        "star slipped through and floated to her pillow. \"Don't be afraid,\" it whispered, "
        "glowing softly. \"I'll stay until you fall asleep.\" Lily smiled. The little star "
        "began humming a gentle song, and by the third note, she was drifting into the "
        "warmest dream."
    ),
    "de": (
        "Mila lag in ihrem kuscheligen Bett, als der Sandmann durch das Fenster sah. Sanft "
        "streute er goldenen Schlafsand über ihr Kissen. \"Hab keine Angst\", flüsterte er, "
        "\"ich bleibe bei dir, bis du sanft eingeschlafen bist.\" Mila lächelte. Ihre Augen "
        "wurden schwer, und schon nach drei Atemzügen träumte sie vom Mond, der für sie "
        "leuchtete."
    ),
    "es": (
        "Sofía estaba acurrucada en su cama cuando la Luna asomó por la ventana. Una "
        "lucecita plateada bajó hasta su almohada. \"No tengas miedo\", susurró con un "
        "brillo cálido. \"Me quedaré contigo hasta que te duermas.\" Sofía sonrió. La "
        "lucecita comenzó a cantar muy bajito, y antes de la tercera nota ya soñaba con "
        "estrellas."
    ),
    "fr": (
        "Chloé était blottie dans son lit quand la Lune apparut à la fenêtre. Une petite "
        "luciole d'argent glissa jusqu'à son oreiller. \"N'aie pas peur\", murmura-t-elle "
        "doucement. \"Je reste avec toi jusqu'à ce que tu t'endormes.\" Chloé sourit. La "
        "luciole se mit à fredonner, et au troisième murmure, elle rêvait déjà d'une nuit "
        "étoilée."
    ),
    "it": (
        "Sofia era raggomitolata nel suo lettino quando una piccola lucciola apparve "
        "alla finestra. Volò piano fino al cuscino con la sua luce dorata. \"Non aver "
        "paura\", sussurrò, \"rimango qui finché non ti addormenti.\" Sofia sorrise. La "
        "lucciola iniziò a canticchiare una ninna nanna, e al terzo bagliore stava già "
        "sognando il cielo stellato."
    ),
    "pl": (
        "Zosia leżała w przytulnym łóżeczku, gdy przy oknie zjawił się Anioł Stróż. "
        "Cichutko nachylił się nad poduszką. \"Nie bój się, maleńka\", szepnął, \"będę "
        "tu, póki nie zaśniesz.\" Zosia uśmiechnęła się. Anioł zaczął nucić starą "
        "kołysankę, a przy trzeciej nucie Zosia już śniła o gwiazdach tańczących nad "
        "łąką."
    ),
    "pt-BR": (
        "Alice estava aconchegada na sua caminha quando uma luzinha dourada surgiu pela "
        "janela. Era um pequeno vaga-lume mágico que pousou no seu travesseiro. \"Não tenha "
        "medo\", ele sussurrou, brilhando suave. \"Fico aqui até você dormir.\" Alice "
        "sorriu. O vaga-lume começou a cantar uma cantiga, e na terceira nota ela já "
        "sonhava com a lua sorridente."
    ),
    "tr": (
        "Elif yumuşacık yatağına kıvrılmıştı ki pencereden minik bir yıldız süzüldü. "
        "Yastığının üzerine usulca kondu. \"Korkma\", diye fısıldadı sıcacık bir ışıkla. "
        "\"Uyuyana kadar yanındayım.\" Elif gülümsedi. Yıldız tatlı bir ninni mırıldanmaya "
        "başladı, ve üçüncü notada Elif çoktan yıldızlı bir gökyüzü düşlüyordu."
    ),
    "ja": (
        "ゆきちゃんがふかふかのお布団に潜ると、窓から金色の光がそっとさしこみました。"
        "それは小さな星のお友だち。「こわがらないで」と優しくささやきました。"
        "「あなたが眠るまで、ここにいるよ。」ゆきちゃんは微笑みました。"
        "星はやさしい子守唄を歌い始め、三つ目の音色には、もう星空の夢を見ていました。"
    ),
    "ko": (
        "보미가 포근한 이불 속으로 들어가자, 창문 사이로 작은 빛이 들어왔어요. "
        "베개 위에 살포시 내려앉은 것은 작은 별 친구였답니다. "
        "\"무서워하지 마,\" 별이 부드럽게 속삭였어요. \"네가 잠들 때까지 있어 줄게.\" "
        "보미가 미소 지었어요. 별이 자장가를 흥얼거리기 시작했고, "
        "세 번째 음에 보미는 이미 별이 가득한 꿈속을 거닐고 있었어요."
    ),
    "ar": (
        "كانت ليلى تحت لحافها الدافئ عندما تسلل ضوء فضي من الهلال عبر الشباك. "
        "نجمة صغيرة هبطت بهدوء فوق وسادتها. \"لا تخافي،\" همست بدفء، "
        "\"سأبقى معك حتى تنامي.\" ابتسمت ليلى. بدأت النجمة تدندن بأغنية حنونة، "
        "ومع النغمة الثالثة كانت ليلى تحلم بسماء مليئة بالنجوم."
    ),
}

# Per-locale 3-scene illustration prompts. Each starts with a cultural environment.
# Same narrative structure: (1) child tucked in, (2) magical helper arrives, (3) child dreaming.
COMMON_NEG = (
    " STRICT: no faces visible (child viewed from behind / face turned away), "
    "no text, no captions, no logos. 16:9 cinematic composition, soft warm lighting, "
    "Pixar 3D animated illustration style. Warm purple/pink/gold accent palette."
)

CULTURAL_SCENES = {
    "en": [
        "A cosy Western-style children's bedroom at bedtime. A small child tucked into bed, only the back of the head visible above a soft pink-purple duvet covered in tiny stars. A round window shows the night sky with a glowing crescent moon. A mushroom-shaped night light glows on the bedside table.",
        "Same Western-style children's bedroom. A tiny glowing silver-gold star hovers above the pillow next to the sleeping child (face turned away). The star casts magical sparkles into the cosy room.",
        "Same bedroom now in dreamy late-night light. The child sleeps peacefully (back view), the tiny star resting on the pillow as a soft nightlight. Music notes drift gently in the air. Stars shine through the window.",
    ],
    "de": [
        "A cosy traditional Bavarian/Alpine children's bedroom with wooden beams and a small wooden bed with cosy pink duvet. A child tucked in (back view, face hidden). Through a small wooden-framed window: starry night sky with the moon. A small carved wooden owl figurine on the bedside table.",
        "Same Bavarian bedroom. A tiny shimmering figure of the Sandmann (sand-man, friendly spirit) gently sprinkles golden sleep-sand from a small pouch above the pillow next to the sleeping child (face away). Magical golden particles fill the air softly.",
        "Same wooden Bavarian bedroom in dreamy late-night light. Child sleeping peacefully under the pink duvet (back view). The Sandmann figurine sits as a tiny nightlight on the pillow, golden sand-traces fading into dreams.",
    ],
    "es": [
        "A cosy Mediterranean Spanish children's bedroom with white-washed walls, terracotta tiled floor, and an arched window showing a warm starry Andalusian night sky with the moon. A small child tucked in under a pink-purple duvet, back view, face hidden. A small ceramic lamp casts warm honey-gold light.",
        "Same Spanish bedroom. A tiny silvery moon-fairy (luna) gently floats above the pillow next to the sleeping child (face away). Soft sparkles and warm Mediterranean light fill the room.",
        "Same room in dreamy late-night light. The child sleeps peacefully (back view), the little moon-fairy curled on the pillow as a gentle nightlight. Stars visible through the arched window.",
    ],
    "fr": [
        "A cosy French children's bedroom with classic mansard ceiling and a small Parisian-style window showing a starry Paris night sky with the moon and rooftops. A child tucked in under a soft pink-purple duvet (back view, face hidden). A small antique lamp on the bedside table casts warm light.",
        "Same French bedroom. A tiny silver firefly (luciole) glows softly above the pillow next to the sleeping child (face away). Magical golden light fills the room.",
        "Same room in dreamy late-night light. The child sleeps peacefully (back view), the little firefly resting on the pillow as a tiny nightlight. Stars and rooftops visible through the window.",
    ],
    "it": [
        "A cosy Italian children's bedroom with classic terracotta tiled floor, painted wooden bedframe, and an open shutter window showing a warm Tuscan starry night sky with the moon. A child tucked in under a pink-purple duvet (back view, face hidden). A small ceramic lamp casts warm Mediterranean glow.",
        "Same Italian bedroom. A tiny golden firefly (lucciola) hovers above the pillow next to the sleeping child (face away). Magical warm Mediterranean sparkles fill the room.",
        "Same room in dreamy late-night light. Child sleeping peacefully (back view), the lucciola resting on the pillow as a soft nightlight. Stars visible through the open shutter window.",
    ],
    "pl": [
        "A cosy traditional Polish children's bedroom with whitewashed walls, dark wood furniture, and a small window showing a starry Polish night sky with the moon. A child tucked in under a pink-purple duvet (back view, face hidden). A small painted folk-art lamp casts warm light. Traditional Polish folk decorative motifs on the wall.",
        "Same Polish bedroom. A tiny glowing guardian angel (Anioł Stróż) silhouette hovers softly above the pillow next to the sleeping child (face away). Golden warm light fills the room.",
        "Same room in dreamy late-night light. Child sleeping peacefully (back view), the small glowing angel sitting near the pillow as a gentle nightlight. Stars shine through the window.",
    ],
    "pt-BR": [
        "A cosy Brazilian children's bedroom with bright tropical accent colors, wooden ceiling fan blades, and a window showing a warm tropical night sky with the moon and palm fronds. A child tucked in under a pink-purple duvet (back view, face hidden). A small colorful folk-art lamp casts warm honey light.",
        "Same Brazilian bedroom. A tiny golden magical firefly (vaga-lume) hovers above the pillow next to the sleeping child (face away). Tropical magical sparkles fill the room.",
        "Same room in dreamy late-night light. Child sleeping peacefully (back view), the vaga-lume resting on the pillow as a small nightlight. Tropical stars and palm silhouettes visible through the window.",
    ],
    "tr": [
        "A cosy traditional Turkish children's bedroom with hand-woven Anatolian kilim rug on the floor, mashrabiya-style wooden window with intricate carved geometric patterns showing a starry Bosphorus night sky with the crescent moon. A child tucked in under a pink-purple duvet (back view, face hidden). A small ceramic Iznik-tile lamp casts warm amber light.",
        "Same Turkish bedroom. A tiny glowing silver crescent-shaped fairy hovers above the pillow next to the sleeping child (face away). Magical warm sparkles fill the room with Turkish lantern patterns of light.",
        "Same room in dreamy late-night light. Child sleeping peacefully (back view), the little crescent fairy resting on the pillow as a soft nightlight. Stars visible through the mashrabiya window.",
    ],
    "ja": [
        "A cosy Japanese-style children's bedroom (washitsu) with tatami mat floor, a small futon on the floor, paper-lantern (chōchin) soft warm light, and a round shoji-style window showing a starry Japanese night sky with the crescent moon and cherry blossom branch silhouette. A child tucked in under a soft pink-purple futon cover (back view, face hidden).",
        "Same Japanese tatami bedroom. A tiny glowing silver-gold star spirit hovers above the futon pillow next to the sleeping child (face away). Magical sakura petals drift gently in golden light.",
        "Same washitsu room in dreamy late-night light. Child sleeping peacefully on the futon (back view), the tiny star spirit resting on the pillow as a gentle nightlight. Cherry blossom and stars visible through the shoji window.",
    ],
    "ko": [
        "A cosy modern Korean children's bedroom with ondol-warm wooden floor, a low bed with pink-purple duvet, and a window showing a starry Seoul night sky with the moon. A child tucked in (back view, face hidden). A small ceramic moon-jar style night lamp casts warm honey-amber light.",
        "Same Korean bedroom. A tiny glowing silver-gold star friend hovers above the pillow next to the sleeping child (face away). Magical warm sparkles fill the room.",
        "Same room in dreamy late-night light. Child sleeping peacefully (back view), the little star friend resting on the pillow as a gentle nightlight. Stars visible through the window.",
    ],
    "ar": [
        "A cosy traditional Middle-Eastern children's bedroom with intricate mashrabiya wooden lattice window casting geometric shadow patterns. A child tucked in under a pink-purple duvet on a low wooden bed (back view, face hidden). A Moroccan-style metal lantern (fanous) casts warm golden patterned light. Through the lattice: a starry desert night sky with crescent Hilal moon.",
        "Same Middle-Eastern bedroom. A tiny glowing silver star descends from the crescent moon and hovers above the pillow next to the sleeping child (face away). The lantern patterns of light intensify with magic.",
        "Same room in dreamy late-night light. Child sleeping peacefully (back view), the little star resting on the pillow as a soft nightlight. The mashrabiya casts gentle magical patterns across the room. Stars and crescent visible through the lattice.",
    ],
}


async def generate_scene_for_locale(locale: str, scene_idx: int, prompt: str) -> Path:
    img_path = WORK / f"{locale}_{scene_idx}.png"
    if img_path.exists():
        logger.info(f"  [{locale}] scene{scene_idx}: cached")
        return img_path
    from engine.image_generator import _call_image_api
    full_prompt = prompt + COMMON_NEG
    data = await _call_image_api(
        content=[{"type": "text", "text": full_prompt}],
        scene_index=scene_idx,
        style_label="painted",
        story_id=None,
    )
    if not data:
        raise RuntimeError(f"image gen returned None for {locale} scene {scene_idx}")
    img_path.write_bytes(data)
    logger.info(f"  [{locale}] scene{scene_idx}: ✓ ({len(data)}B)")
    return img_path


async def tts_one(locale: str, text: str, out_path: Path):
    api = os.environ["ELEVENLABS_API_KEY"]
    vid, vname = CURATED_VOICES[locale]
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{vid}"
    payload = {
        "text": text,
        "model_id": "eleven_v3",
        "language_code": EL_LANG_CODE[locale],
        "voice_settings": {"stability": 0.50, "similarity_boost": 0.85, "style": 0.25},
    }
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=payload,
                          headers={"xi-api-key": api, "Content-Type": "application/json", "Accept": "audio/mpeg"},
                          timeout=aiohttp.ClientTimeout(total=120)) as r:
            if r.status != 200:
                body = (await r.read())[:300]
                raise RuntimeError(f"TTS {r.status}: {body!r}")
            out_path.write_bytes(await r.read())
    logger.info(f"  [{locale}] TTS '{vname}' → {out_path.stat().st_size}B")


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
    logger.info(f"[{locale}] MP4 {out_path.stat().st_size}B duration={dur:.1f}s")


async def run_locale(locale: str, force: bool):
    out_mp4 = STATIC / f"{locale}.mp4"
    if out_mp4.exists() and not force:
        logger.info(f"[{locale}] skip (exists)")
        return
    logger.info(f"[{locale}] starting…")
    story = MASTER_STORIES[locale]
    scenes_prompts = CULTURAL_SCENES[locale]
    scenes = []
    for i, p in enumerate(scenes_prompts):
        scenes.append(await generate_scene_for_locale(locale, i, p))
    audio = WORK / f"{locale}.mp3"
    if not audio.exists() or force:
        await tts_one(locale, story, audio)
    await make_video(locale, scenes, audio, out_mp4)


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--locale", help="run for one locale only")
    p.add_argument("--force", action="store_true")
    args = p.parse_args()
    targets = [args.locale] if args.locale else LOCALES
    logger.info(f"Targets: {targets}")
    for loc in targets:
        try:
            await run_locale(loc, args.force)
        except Exception as e:
            logger.error(f"[{loc}] FAILED: {e}", exc_info=True)
    logger.info(f"Done. Outputs in {STATIC}")


if __name__ == "__main__":
    asyncio.run(main())
