#!/usr/bin/env python3
"""Add chip_puppy + mic / sticky / honeypot keys to 11 locales."""
import asyncio, json, logging, os
from pathlib import Path
import aiohttp

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("create-keys")

OUT = Path("/app/web/locales")
LOCALES = ["en","de","es","fr","it","pl","pt-BR","tr","ja","ko","ar"]

EN_KEYS = {
    "chip_puppy":      "🐶 Wants a puppy",
    "chip_puppy_full": "A story about Mia, 5 years old, who really wants a puppy. Let her perform a small act of kindness and prove she's ready to care for a pet.",
    "mic_record":      "🎙 Record by voice",
    "mic_stop":        "⏹ Stop recording",
    "mic_starting":    "⏳ Starting mic…",
    "mic_processing":  "⏳ Transcribing…",
    "mic_no_support":  "Voice input is not supported in this browser",
    "mic_no_perm":     "Couldn't access the microphone. Please allow microphone access.",
    "mic_failed":      "Couldn't transcribe — please try again.",
    "topic_voice_hint":"Tap 🎙 to dictate. The narrator may read the surname with a different stress.",
    "sticky_note":     "📝 Free text first · 💜 Pay only if you love it",
}

OPENROUTER = "https://openrouter.ai/api/v1/chat/completions"
LANG_NAMES = {
    "de":"German","es":"Spanish","fr":"French","it":"Italian","pl":"Polish",
    "pt-BR":"Brazilian Portuguese","tr":"Turkish","ja":"Japanese",
    "ko":"Korean","ar":"Modern Standard Arabic",
}


async def translate(locale: str, lang_name: str):
    api = os.environ["OPENROUTER_API_KEY"]
    prompt = (
        f"Translate the following Lalaka /create-page strings into {lang_name}. "
        f"For chip_puppy_full: replace 'Mia' with a culturally appropriate name for {lang_name}. "
        "Preserve emojis, HTML tags. Return ONLY valid JSON with same keys.\n\n"
        f"INPUT:\n{json.dumps(EN_KEYS, ensure_ascii=False, indent=2)}\n\n"
        "Return ONLY the JSON."
    )
    body = {
        "model": "google/gemini-2.5-flash",
        "messages": [
            {"role": "system", "content": f"You are an expert {lang_name} translator for children's products."},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 2500, "temperature": 0.15,
    }
    async with aiohttp.ClientSession() as s:
        async with s.post(OPENROUTER, json=body, headers={"Authorization": f"Bearer {api}"},
                          timeout=aiohttp.ClientTimeout(total=180)) as r:
            data = await r.json()
    content = data["choices"][0]["message"]["content"].strip()
    s = content
    if s.startswith("```"):
        s = s.split("```", 2)[1]
        if s.startswith("json"):
            s = s[4:]
        s = s.strip("` \n")
    return json.loads(s)


async def main():
    p = OUT / "en.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    for k, v in EN_KEYS.items(): data[k] = v
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logger.info(f"  en: +{len(EN_KEYS)}")

    for loc in LOCALES:
        if loc == "en": continue
        try: translated = await translate(loc, LANG_NAMES[loc])
        except Exception as e:
            logger.error(f"{loc}: {e}")
            continue
        p = OUT / f"{loc}.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        for k in EN_KEYS:
            data[k] = translated.get(k) or EN_KEYS[k]
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        logger.info(f"  {loc}: ok")


if __name__ == "__main__":
    asyncio.run(main())
