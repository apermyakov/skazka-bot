#!/usr/bin/env python3
"""Add email-template translation keys (subject + body) for 11 locales.

3 emails: text_ready_invite, story_ready, followup_rating.
EN baseline + LLM batch translate.
"""
import asyncio, json, logging, os
from pathlib import Path
import aiohttp

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("email-keys")

OUT = Path("/app/web/locales")
LOCALES = ["en","de","es","fr","it","pl","pt-BR","tr","ja","ko","ar"]

EN_KEYS = {
    # Free text preview ready
    "email_text_ready_subject": "📝 Your story «{title}» is ready to read",
    "email_text_ready_h1":      "📝 Your story text is ready",
    "email_text_ready_body":    "We've written your story «{title}». Open it now to read and decide whether to turn it into a full audio fairy tale with narration, illustrations and video.",
    "email_text_ready_cta":     "✨ Open my story",
    "email_text_ready_perks":   "<b>What you get for {price}:</b><br>🎙 Professional voice narration · 🎨 10–15 warm illustrations · 🎬 HD video · 🛡️ Money-back guarantee",
    "email_text_ready_foot":    "The text stays at the link — come back whenever you like.",

    # Story fully ready (after payment)
    "email_story_ready_subject":"✨ «{title}» — your fairy tale is ready!",
    "email_story_ready_h1":     "✨ Your fairy tale is ready!",
    "email_story_ready_body":   "«{title}» is now a full audio story with voice narration, illustrations and HD video — created just for your child.",
    "email_story_ready_cta":    "▶ Watch & listen",
    "email_story_ready_perks":  "Tip: watch together on a tablet/TV at bedtime, or just listen on a phone. The whole bundle is saved at the link below.",

    # Follow-up rating ask (24h after delivery)
    "email_rate_subject":       "How was «{title}»?",
    "email_rate_h1":            "How did your child like the story?",
    "email_rate_body":          "Yesterday we sent you «{title}». We'd love to know how your child took it — your feedback shapes what we build next.",
    "email_rate_cta":           "💜 Leave a rating",

    # Shared footer
    "email_footer_team":        "With warmth,<br>the Lalaka team",
    "email_footer_addr":        "Airotie 2 C, Helsinki, Uusimaa 4524116, Finland",
    "email_footer_unsub":       "If you don't want to receive these updates, reply to this email and we'll remove you immediately.",
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
        f"Translate Lalaka email-template strings to {lang_name}. Preserve {{placeholders}} "
        "verbatim (will be filled at send time). Preserve HTML tags, emojis. Warm parental "
        f"register. Return ONLY valid JSON with same keys.\n\n"
        f"INPUT:\n{json.dumps(EN_KEYS, ensure_ascii=False, indent=2)}\n\n"
        "Return ONLY the JSON."
    )
    body = {
        "model": "google/gemini-2.5-flash",
        "messages": [
            {"role": "system", "content": f"You are an expert {lang_name} translator for customer emails."},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 3500, "temperature": 0.15,
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
