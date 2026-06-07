#!/usr/bin/env python3
"""Native-quality review pass on Lalaka UI translations.

For each non-EN locale: send the EN source + current translation to Gemini Flash
with a native-reviewer prompt; ask for high-confidence improvements only.
Apply changes to the locale JSON only when the LLM is confident.

Costs ~3-5k tokens per locale × 12 locales = ~50k tokens total.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, "/app")

import aiohttp

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("review-translations")

OUT = Path("/app/web/locales")
LANGS = [
    ("de", "German"), ("es", "Spanish (Spain/LatAm neutral)"), ("fr", "French"),
    ("it", "Italian"), ("pl", "Polish"), ("pt-BR", "Brazilian Portuguese"),
    ("tr", "Turkish"), ("ja", "Japanese"), ("ko", "Korean"),
    ("ar", "Modern Standard Arabic"), ("ru", "Russian"), ("uk", "Ukrainian"),
]

# Skip rebrand-prone or technical keys
SKIP = {"page_title", "tagline", "footer_rights", "lang_picker"}

OPENROUTER = "https://openrouter.ai/api/v1/chat/completions"

PROMPT_TEMPLATE = """You are a NATIVE speaker of {language} reviewing UI translations for a children's audio-fairy-tale product called Lalaka.

For each key below, you have:
  - "en": the English source string
  - "loc": the current {language} translation

Your job: improve translations that sound unnatural, awkward, machine-translated, or grammatically odd. Keep the warm/personal tone for parents of young children. Preserve emoji, brand name "Lalaka", and template placeholders like {{price}}.

ONLY change a translation if you are HIGHLY CONFIDENT the change is an improvement a native speaker would prefer. Don't change for style preference — only fix real issues. If current is fine, return null for that key.

Return ONLY a JSON object: {{ "key1": "new translation or null", "key2": "new translation or null", ... }}

No markdown, no commentary. ALL keys from the input must appear in your response.

INPUT:
{payload}
"""


async def review_locale(loc_code: str, lang_name: str):
    en_path = OUT / "en.json"
    loc_path = OUT / f"{loc_code}.json"
    if not loc_path.exists():
        logger.warning("%s: file missing, skip", loc_code)
        return
    en_data = json.loads(en_path.read_text(encoding="utf-8"))
    loc_data = json.loads(loc_path.read_text(encoding="utf-8"))

    # Build payload: en + current translation for every key (except SKIP)
    payload = {}
    for k in en_data:
        if k in SKIP:
            continue
        payload[k] = {"en": en_data[k], "loc": loc_data.get(k, "")}

    prompt = PROMPT_TEMPLATE.format(
        language=lang_name,
        payload=json.dumps(payload, ensure_ascii=False, indent=2),
    )

    api = os.environ["OPENROUTER_API_KEY"]
    body = {
        "model": "google/gemini-2.5-flash",
        "messages": [
            {"role": "system", "content": f"You are an expert native {lang_name} translator and editor for children's products."},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 8000,
        "temperature": 0.2,
    }
    async with aiohttp.ClientSession() as s:
        async with s.post(OPENROUTER, json=body,
                          headers={"Authorization": f"Bearer {api}"},
                          timeout=aiohttp.ClientTimeout(total=180)) as r:
            data = await r.json()
            try:
                content = data["choices"][0]["message"]["content"].strip()
            except (KeyError, IndexError):
                logger.error("%s: bad LLM response: %s", loc_code, str(data)[:300])
                return

    # Parse JSON
    s = content
    if s.startswith("```"):
        s = s.split("```", 2)[1]
        if s.startswith("json"):
            s = s[4:]
        s = s.strip("` \n")
    try:
        fixes = json.loads(s)
    except json.JSONDecodeError as e:
        logger.error("%s: failed to parse fixes JSON: %s", loc_code, e)
        return

    applied = 0
    for key, new_val in fixes.items():
        if not new_val or new_val == loc_data.get(key):
            continue
        if not isinstance(new_val, str):
            continue
        # Sanity: preserve {price} placeholder when source has it
        if "{price}" in en_data.get(key, "") and "{price}" not in new_val:
            logger.warning("%s/%s: rejected fix (lost {price} placeholder)", loc_code, key)
            continue
        old = loc_data.get(key, "")
        loc_data[key] = new_val
        applied += 1
        if applied <= 5:
            logger.info("  %s/%s: %r → %r", loc_code, key, old[:60], new_val[:60])

    loc_path.write_text(json.dumps(loc_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logger.info("%s: applied %d fixes", loc_code, applied)


async def main():
    for loc, lang in LANGS:
        try:
            await review_locale(loc, lang)
        except Exception as e:
            logger.error("%s: failed: %s", loc, e, exc_info=True)


if __name__ == "__main__":
    asyncio.run(main())
