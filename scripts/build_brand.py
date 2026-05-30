"""Take the chosen logo source files and prepare a complete brand kit:
- logo_horizontal.png (transparent, cropped, for header)
- favicon.svg / favicon-16/32.png / apple-touch-icon-180.png / icon-192/512.png
- og_card.jpg (1200x630, with new logo)
"""
import io
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

ROOT = Path("/app")
SRC_PRIMARY = ROOT / "assets/logo_candidates_gpt/logo_03.png"  # icon + wordmark
SRC_ICON = ROOT / "assets/logo_candidates_gpt/logo_06.png"     # icon only
STATIC = ROOT / "web/static"
STATIC.mkdir(parents=True, exist_ok=True)


def trim_whitespace(img: Image.Image, bg_threshold: int = 245) -> Image.Image:
    """Crop near-white margins down to the logo's bounding box, leaving small pad."""
    img = img.convert("RGBA")
    px = img.load()
    w, h = img.size
    min_x, min_y, max_x, max_y = w, h, 0, 0
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a > 30 and not (r > bg_threshold and g > bg_threshold and b > bg_threshold):
                if x < min_x: min_x = x
                if x > max_x: max_x = x
                if y < min_y: min_y = y
                if y > max_y: max_y = y
    pad = 20
    box = (max(0, min_x - pad), max(0, min_y - pad),
           min(w, max_x + pad), min(h, max_y + pad))
    return img.crop(box)


def white_to_alpha(img: Image.Image, threshold: int = 240) -> Image.Image:
    """Turn near-white pixels transparent so the logo sits cleanly on any bg."""
    img = img.convert("RGBA")
    data = img.getdata()
    new = []
    for r, g, b, a in data:
        if r >= threshold and g >= threshold and b >= threshold:
            new.append((r, g, b, 0))
        else:
            new.append((r, g, b, a))
    img.putdata(new)
    return img


def build_header_logo():
    img = Image.open(SRC_PRIMARY)
    img = white_to_alpha(img)
    img = trim_whitespace(img)
    # Resize to a sane web size (height ~ 64px @ 2x = 128)
    target_h = 200
    w, h = img.size
    new_w = int(w * target_h / h)
    img = img.resize((new_w, target_h), Image.LANCZOS)
    out = STATIC / "logo_horizontal.png"
    img.save(out, "PNG", optimize=True)
    print(f"  ✓ {out} ({img.size[0]}×{img.size[1]})")


def build_favicons():
    src = Image.open(SRC_ICON)
    src = white_to_alpha(src)
    src = trim_whitespace(src)
    # Make square (centered on canvas)
    w, h = src.size
    side = max(w, h) + 60
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(src, ((side - w) // 2, (side - h) // 2), src)
    # Multiple sizes
    sizes = {
        "favicon-16.png": 16,
        "favicon-32.png": 32,
        "apple-touch-icon-180.png": 180,
        "icon-192.png": 192,
        "icon-512.png": 512,
    }
    for name, sz in sizes.items():
        out = STATIC / name
        canvas.resize((sz, sz), Image.LANCZOS).save(out, "PNG", optimize=True)
        print(f"  ✓ {out} ({sz}×{sz})")
    # ICO containing 16+32+48
    ico_out = STATIC / "favicon.ico"
    canvas.resize((48, 48), Image.LANCZOS).save(
        ico_out, format="ICO", sizes=[(16, 16), (32, 32), (48, 48)])
    print(f"  ✓ {ico_out}")


def build_og_card():
    """1200x630 OG card with new icon + brand."""
    W, H = 1200, 630
    bg = Image.new("RGB", (W, H), (43, 35, 80))
    # Subtle gradient + soft glow
    overlay = Image.new("RGB", (W, H), (124, 92, 255))
    bg = Image.blend(bg, overlay, 0.18)
    # Soft purple glow circle bottom-right
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([W - 600, H - 400, W + 100, H + 200], fill=(180, 138, 255, 70))
    glow = glow.filter(ImageFilter.GaussianBlur(80))
    bg.paste(glow.convert("RGB"), (0, 0), glow)

    # Paste the icon on top-left
    icon = Image.open(SRC_ICON)
    icon = white_to_alpha(icon)
    icon = trim_whitespace(icon)
    iw, ih = icon.size
    target_h = 110
    new_w = int(iw * target_h / ih)
    icon = icon.resize((new_w, target_h), Image.LANCZOS)
    bg.paste(icon, (68, 56), icon)

    # "Сказик" wordmark next to icon
    F = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    FB = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    draw = ImageDraw.Draw(bg)
    f_brand = ImageFont.truetype(FB, 56)
    draw.text((68 + new_w + 18, 80), "Сказик", font=f_brand, fill=(255, 255, 255))

    # Headline
    f_h1 = ImageFont.truetype(FB, 68)
    draw.text((68, 220), "Сказка, где главный", font=f_h1, fill=(255, 255, 255))
    draw.text((68, 300), "герой — ваш ребёнок", font=f_h1, fill=(255, 158, 200))

    # Sub
    f_sub = ImageFont.truetype(F, 30)
    draw.text((68, 410), "Аудио + иллюстрации + видео по фото и теме.",
              font=f_sub, fill=(220, 210, 255))
    draw.text((68, 450), "Текст сказки — бесплатно.",
              font=f_sub, fill=(220, 210, 255))

    # CTA pill bottom-left
    f_cta = ImageFont.truetype(FB, 28)
    cta_x, cta_y, cta_w, cta_h = 68, 540, 310, 60
    draw.rounded_rectangle([cta_x, cta_y, cta_x + cta_w, cta_y + cta_h],
                            radius=16, fill=(255, 255, 255))
    bbox = draw.textbbox((0, 0), "Создать сказку →", font=f_cta)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text((cta_x + (cta_w - tw) // 2, cta_y + (cta_h - th) // 2 - 4),
              "Создать сказку →", font=f_cta, fill=(124, 92, 255))

    # URL bottom-right
    url_bbox = draw.textbbox((0, 0), "skazik.app", font=f_cta)
    draw.text((W - 68 - (url_bbox[2] - url_bbox[0]),
               cta_y + (cta_h - (url_bbox[3] - url_bbox[1])) // 2 - 4),
              "skazik.app", font=f_cta, fill=(220, 210, 255))

    out = STATIC / "og_card.jpg"
    bg.save(out, "JPEG", quality=88, optimize=True)
    print(f"  ✓ {out}")


if __name__ == "__main__":
    print("Building header logo…"); build_header_logo()
    print("Building favicons…"); build_favicons()
    print("Building OG card…"); build_og_card()
    print("Done.")
