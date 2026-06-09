#!/usr/bin/env python3
"""Regen scene0/1/2 PNGs per locale with VISUAL VARIETY (no more girl-in-bed-everywhere).

Scene 0 — cosy bedroom (story setup, child being tucked in)
Scene 1 — MAGICAL OUTDOOR ADVENTURE (forest/sky/river — completely different setting)
Scene 2 — ENCHANTED MEADOW / MAGICAL CLOUD finale with helper friend

All scenes: image-to-image with {locale}_photo.png as reference for face preservation.
Painted style, no Pixar.

Output: /app/web/static/lalaka_examples/{locale}_scene{0,1,2}.png
Cost: 11 × 3 × $0.04 ≈ $1.3, ~8 min with concurrency.
"""
from __future__ import annotations
import argparse, asyncio, base64, logging, os, sys
from pathlib import Path

sys.path.insert(0, "/app")

REPO = Path("/app")
EXAMPLES = REPO / "web" / "static" / "lalaka_examples"
EXAMPLES.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("varied-scenes")

LOCALES = ["en","de","es","fr","it","pl","pt-BR","tr","ja","ko","ar"]

# Locale-flavoured cosy bedroom for scene 0
BEDROOMS = {
    "en":    "a cosy Western children's bedroom with a round window and a mushroom-shaped night lamp",
    "de":    "a cosy Bavarian/Alpine children's bedroom with wooden beams and a small wooden window",
    "es":    "a cosy Mediterranean Spanish bedroom with whitewashed walls and arched window",
    "fr":    "a cosy Parisian mansard children's bedroom with a small dormer window",
    "it":    "a cosy Italian children's bedroom with terracotta-tiled floor and open shutters",
    "pl":    "a cosy traditional Polish children's bedroom with folk-art motifs",
    "pt-BR": "a cosy Brazilian children's bedroom with tropical accents and a window showing palm fronds",
    "tr":    "a cosy traditional Turkish bedroom with a kilim rug and a mashrabiya window",
    "ja":    "a cosy Japanese washitsu room with tatami, a small futon and shoji window with cherry blossom branch",
    "ko":    "a cosy modern Korean bedroom with a low bed and a moon-jar style night lamp",
    "ar":    "a cosy Middle-Eastern bedroom with a mashrabiya lattice window and a Moroccan fanous lantern",
}

# Magical helper figure per locale (used in scenes 1 & 2)
HELPERS = {
    "en":    "a tiny silver fairy-star with delicate translucent wings",
    "de":    "the Sandmännchen (Sand-man) with a small pouch of golden sleep-sand",
    "es":    "a tiny silvery moon-fairy (Luna) glowing softly",
    "fr":    "a glowing silver firefly (luciole) leaving a sparkling trail",
    "it":    "a tiny golden firefly (lucciola) wrapped in warm light",
    "pl":    "a small glowing guardian angel (Anioł Stróż) silhouette with soft wings",
    "pt-BR": "a tiny magical golden firefly (vaga-lume mágico)",
    "tr":    "a small silver crescent-shaped fairy",
    "ja":    "a tiny glowing silver-gold star spirit trailing cherry blossom petals",
    "ko":    "a small star friend with soft golden light",
    "ar":    "a tiny silver star descended from the Hilal crescent moon",
}

PAINTED = (
    "CLASSIC HAND-PAINTED FAIRY-TALE STORYBOOK ILLUSTRATION — rich gouache and oil painting "
    "with visible brushwork, warm golden light, painterly textures, the timeless look of a "
    "treasured children's picture book. Painterly, NOT flat vector, NOT photographic, NOT Pixar "
    "3D, NOT digital cartoon."
)

FACE_LOCK = (
    "The child's face MUST be recognisably the SAME child as in the reference photo — same hair "
    "colour and style, same eye colour, same skin tone, same facial features and same age. "
    "Preserve identity from the photo."
)

def scene_prompt(locale: str, idx: int) -> str:
    bedroom = BEDROOMS[locale]
    helper = HELPERS[locale]
    if idx == 0:
        action = (
            f"Story setup, cosy bedtime scene. Wide landscape (16:9). The child is in {bedroom}, "
            "sitting up in bed under a soft purple-pink duvet, smiling gently. A small picture-book "
            "is open beside her. The bedside lamp glows softly. Through the window: a starry night "
            "with a crescent moon. Warm cosy mood, painterly brushwork."
        )
    elif idx == 1:
        action = (
            "MAGICAL DREAM ADVENTURE — completely different setting from the bedroom. Wide "
            f"landscape (16:9). The child is FLYING through an ENCHANTED NIGHT FOREST alongside "
            f"{helper}. Tall whispering trees with glowing mushrooms below. Friendly forest "
            "creatures (a fox, an owl, fireflies) peek from branches. Silver moon glows above the "
            "canopy. A trail of golden sparkles follows the child's flight. Mood: wonder, magic, "
            "wide-eyed delight. The child's arms are spread joyfully."
        )
    elif idx == 2:
        action = (
            "FINALE — magical RIVER and STARLIT SKY scene. Wide landscape (16:9). The child is "
            f"sitting on a SOFT WHITE CLOUD floating gently above a shimmering moonlit RIVER and "
            f"meadow, with {helper} beside her. Distant rolling hills, a sky full of swirling "
            "constellations and falling stars. Below: lily pads, a sleeping deer, a gentle waterfall. "
            "The child smiles dreamily, blanket draped around her shoulders. Mood: peaceful awe, "
            "the magic settling toward sleep."
        )
    elif idx == 3:
        action = (
            "UNDERWATER PALACE adventure. Wide landscape (16:9). The child is swimming gracefully "
            "inside a glowing CORAL PALACE in a warm sapphire-blue ocean. Schools of luminous "
            "tropical fish, a friendly silver dolphin, and gently swaying sea-plants surround her. "
            f"{helper} hovers nearby in a bubble. Shafts of moonlight pierce the water from above. "
            "Mood: wonder, weightlessness, a treasure-room glow. The child laughs in delight."
        )
    elif idx == 4:
        action = (
            "MOUNTAIN-PEAK CAMPFIRE scene. Wide landscape (16:9). The child sits on a soft "
            "blanket on a high meadow at the very top of a snow-dusted mountain, beside a small "
            f"crackling campfire. {helper} floats just above the flames. Below: a sea of clouds "
            "stretching to the horizon, mountain silhouettes poking through. Above: an enormous "
            "AURORA BOREALIS painting the sky in greens, purples, pinks. A friendly mountain "
            "owl perched on a nearby rock. Mood: hush, awe, the world far below."
        )
    else:  # idx == 5
        action = (
            "FLOATING SKY-CASTLE garden. Wide landscape (16:9). The child stands in a "
            "BLOSSOMING GARDEN built atop a floating sky-castle in a sunset-violet sky. "
            "Hot-air balloons drift in the distance, friendly little dragons curl on the "
            f"battlements, butterfly-clouds float past. {helper} circles around her with a "
            "trail of golden sparkles. Tall flowers taller than the child sway in a warm breeze. "
            "Mood: joyful imagination, late-afternoon golden hour, fairy-tale grandeur."
        )
    return f"{action}\n\n{FACE_LOCK}\n\nSTYLE: {PAINTED}\n\nWide cinematic 16:9 landscape composition."


async def call_image_api(prompt: str, photo_b64: str) -> bytes:
    import aiohttp, json
    api_key = os.environ["OPENROUTER_API_KEY"]
    body = {
        "model": "google/gemini-3-pro-image-preview",
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{photo_b64}"}},
            ],
        }],
        "modalities": ["image", "text"],
    }
    timeout = aiohttp.ClientTimeout(total=300)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        async with s.post("https://openrouter.ai/api/v1/chat/completions",
                          json=body,
                          headers={"Authorization": f"Bearer {api_key}"}) as r:
            data = await r.json()
    msg = data["choices"][0]["message"]
    for img in msg.get("images") or []:
        url = img.get("image_url", {}).get("url", "")
        if url.startswith("data:image"):
            return base64.b64decode(url.split(",", 1)[1])
    raise RuntimeError(f"no image in response: {json.dumps(data)[:200]}")


def _png_to_webp(png_bytes: bytes, out_path: Path, quality: int = 82) -> int:
    """Pillow is loaded lazily so the script still imports without it."""
    import io
    from PIL import Image
    im = Image.open(io.BytesIO(png_bytes))
    if im.mode == "RGBA":
        im = im.convert("RGB")
    im.save(out_path, "webp", quality=quality, method=6)
    return out_path.stat().st_size


async def gen_scene(locale: str, idx: int, photo_b64: str, sem: asyncio.Semaphore):
    async with sem:
        out = EXAMPLES / f"{locale}_scene{idx}.webp"
        prompt = scene_prompt(locale, idx)
        log.info(f"  {locale}/scene{idx} → generating…")
        try:
            img = await call_image_api(prompt, photo_b64)
            # Convert the model's PNG response straight to WebP so the gallery
            # stays a tenth the size it would otherwise be.
            written = _png_to_webp(img, out)
            log.info(f"  {locale}/scene{idx} ✓ {len(img)//1024}KB png → {written//1024}KB webp")
        except Exception as e:
            log.error(f"  {locale}/scene{idx} ✗ {e!r}")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--locale", help="Only this locale")
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--idx-range", nargs=2, type=int, default=[0, 3],
                    help="Inclusive-exclusive scene index range, e.g. 3 6 for scenes 3,4,5")
    args = ap.parse_args()

    locs = [args.locale] if args.locale else LOCALES
    sem = asyncio.Semaphore(args.concurrency)
    tasks = []
    for loc in locs:
        photo_path = EXAMPLES / f"{loc}_photo.png"
        if not photo_path.exists():
            log.error(f"  {loc}: photo not found at {photo_path}")
            continue
        photo_b64 = base64.b64encode(photo_path.read_bytes()).decode()
        idx_range = range(*args.idx_range)
        for idx in idx_range:
            tasks.append(asyncio.create_task(gen_scene(loc, idx, photo_b64, sem)))
    await asyncio.gather(*tasks)
    log.info(f"Done. {len(tasks)} scenes generated.")


if __name__ == "__main__":
    asyncio.run(main())
