#!/usr/bin/env python3
"""Add new landing-page keys (hero trust, FAQ, final CTA) to all 11 locales.
EN written by hand. Others translated via LLM batch."""
import asyncio, json, logging, os, sys
from pathlib import Path
import aiohttp

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("landing-keys")

OUT = Path("/app/web/locales")
LOCALES = ["en","de","es","fr","it","pl","pt-BR","tr","ja","ko","ar"]

EN_KEYS = {
    "trust_free_preview":  "📝 Free preview",
    "trust_minutes":       "✨ Ready in 5–8 minutes",
    "trust_guarantee":     "🛡️ Money-back guarantee",
    "faq_h2":              "Frequently asked questions",
    "faq_q1":              "What if my child doesn't like the result?",
    "faq_a1":              "We'll refund you within 24 hours — just write to us via the <a href=\"mailto:hello@lalaka.ai\">contact email</a>, no bureaucracy.",
    "faq_q2":              "How safe is my child's photo?",
    "faq_a2":              "The photo is used only to make the hero look like your child in the illustrations. We never publish photos, never share them with third parties, and never use them to train AI models. You can request deletion any time.",
    "faq_q3":              "How long is the audio story?",
    "faq_a3":              "About 5 minutes of professional voice narration, paced for bedtime. Just enough for a cosy ritual without rushing.",
    "faq_q4":              "What if I want to change something in the story?",
    "faq_a4":              "After the free preview you can rewrite freely — make it shorter, add a character, change the ending. As many times as you like, all free.",
    "faq_q5":              "Can I use the result commercially?",
    "faq_a5":              "The story is for your family's personal enjoyment — playback at home, in the car, on a tablet. For commercial use please email us first.",
    "final_cta_title":     "Give your child their own story",
    "final_cta_sub":       "5 minutes to create, a memory for a lifetime. Free to write, pay only if you love it.",
}

OPENROUTER = "https://openrouter.ai/api/v1/chat/completions"
LANG_NAMES = {
    "de":"German","es":"Spanish (neutral)","fr":"French","it":"Italian","pl":"Polish",
    "pt-BR":"Brazilian Portuguese","tr":"Turkish","ja":"Japanese",
    "ko":"Korean","ar":"Modern Standard Arabic",
}


async def translate(locale: str, lang_name: str):
    api = os.environ["OPENROUTER_API_KEY"]
    prompt = (
        f"Translate the Lalaka landing-page strings into {lang_name}. "
        "Preserve HTML tags verbatim, emojis, punctuation style of target language. "
        "Return ONLY valid JSON with the same keys.\n\n"
        f"INPUT:\n{json.dumps(EN_KEYS, ensure_ascii=False, indent=2)}\n\n"
        "Return ONLY the JSON object."
    )
    body = {
        "model": "google/gemini-2.5-flash",
        "messages": [
            {"role": "system", "content": f"You are an expert {lang_name} translator for children's products."},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 3500, "temperature": 0.15,
    }
    async with aiohttp.ClientSession() as s:
        async with s.post(OPENROUTER, json=body, headers={"Authorization": f"Bearer {api}"},
                          timeout=aiohttp.ClientTimeout(total=180)) as r:
            data = await r.json()
    try:
        content = data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError):
        logger.error(f"{locale}: bad response: {str(data)[:200]}")
        return {}
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
    for k, v in EN_KEYS.items():
        data[k] = v
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logger.info(f"  en: +{len(EN_KEYS)} keys")

    for loc in LOCALES:
        if loc == "en":
            continue
        try:
            translated = await translate(loc, LANG_NAMES[loc])
        except Exception as e:
            logger.error(f"{loc}: {e}")
            continue
        p = OUT / f"{loc}.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        for k in EN_KEYS:
            data[k] = translated.get(k) or EN_KEYS[k]
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        logger.info(f"  {loc}: {len(EN_KEYS)} keys")


if __name__ == "__main__":
    asyncio.run(main())
