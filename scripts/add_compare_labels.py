#!/usr/bin/env python3
"""Add gallery_photo_label + gallery_hero_label keys."""
import json
from pathlib import Path

OUT = Path("/app/web/locales")

LABELS = {
    "en":    {"gallery_photo_label": "Your child's photo",          "gallery_hero_label": "Hero of the story"},
    "de":    {"gallery_photo_label": "Foto Ihres Kindes",           "gallery_hero_label": "Held der Geschichte"},
    "es":    {"gallery_photo_label": "Foto de tu hijo/a",           "gallery_hero_label": "Héroe del cuento"},
    "fr":    {"gallery_photo_label": "Photo de votre enfant",       "gallery_hero_label": "Héros de l'histoire"},
    "it":    {"gallery_photo_label": "Foto di tuo figlio/a",        "gallery_hero_label": "Eroe della storia"},
    "pl":    {"gallery_photo_label": "Zdjęcie Twojego dziecka",     "gallery_hero_label": "Bohater bajki"},
    "pt-BR": {"gallery_photo_label": "Foto do seu filho/a",         "gallery_hero_label": "Herói do conto"},
    "tr":    {"gallery_photo_label": "Çocuğunuzun fotoğrafı",       "gallery_hero_label": "Masalın kahramanı"},
    "ja":    {"gallery_photo_label": "お子様のお写真",                "gallery_hero_label": "物語の主人公"},
    "ko":    {"gallery_photo_label": "우리 아이 사진",               "gallery_hero_label": "이야기의 주인공"},
    "ar":    {"gallery_photo_label": "صورة طفلك",                   "gallery_hero_label": "بطل القصة"},
}
for loc, kv in LABELS.items():
    p = OUT / f"{loc}.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    for k, v in kv.items(): d[k] = v
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  {loc}: ok")
