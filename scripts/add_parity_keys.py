#!/usr/bin/env python3
"""Add Skazik-parity keys: testimonials carousel + 3 more FAQ + gallery + guarantee.
EN baseline + LLM batch translate to 10 locales."""
import asyncio, json, logging, os
from pathlib import Path
import aiohttp

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("parity-keys")

OUT = Path("/app/web/locales")
LOCALES = ["en","de","es","fr","it","pl","pt-BR","tr","ja","ko","ar"]

# Names per locale (we'll inject these into testimonials by template; here we just
# write the EN base — translator will replace names with culturally appropriate.)
EN_KEYS = {
    # Testimonials section
    "testimonials_h2":   "What parents say",
    "t1_text":           "Liam couldn't believe at first that HE was the hero. Played it again to make sure.",
    "t1_who":            "Liam's dad · age 6",
    "t2_text":           "We wanted to buy a personalised book and got a whole video. Sofia watched with shining eyes.",
    "t2_who":            "Sofia's mum · age 4",
    "t3_text":           "Ordered the night before her first day at kindergarten — about a girl who walks in bravely. The morning was calm. Coincidence or not, we breathed easier.",
    "t3_who":            "Mia's mum · age 3",
    "t4_text":           "I was sceptical — it's AI, after all. But the story was warm and well-crafted, no awkwardness. My grandson asks for it every night now.",
    "t4_who":            "Lucas's grandma · age 5",
    "t5_text":           "We had a planned hospital visit and our son was anxious. We made a story about a brave little pirate who isn't afraid of doctors. He listened to it before the visit — it helped.",
    "t5_who":            "Noah's mum · age 4",
    "t6_text":           "My daughter was sick at home for a week. A fairy tale about her became the highlight — she talked through every plot turn with me.",
    "t6_who":            "Olivia's mum · age 5",
    "t7_text":           "Dad's away on a long trip. We made a story where dad comes home through forests and dragons. James only falls asleep to it now.",
    "t7_who":            "James's mum · age 7",
    "t8_text":           "My twins insisted on being BOTH heroes. The form took that — and yes, both lead. No one complained, which is rare.",
    "t8_who":            "Lily and Lena's mum · age 6",

    # Illustration gallery
    "gallery_h2":        "Illustration examples",
    "gallery_sub":       "Upload your child's photo — and they become the hero of the story, recognisable from the first glance.",
    "gallery_caption_1": "Cosy bedtime scene",
    "gallery_caption_2": "Magical helper arrives",
    "gallery_caption_3": "Drifting into dreams",
    "gallery_caption_4": "Same story, different traditions",

    # Guarantee dedicated card
    "guar_seal":         "GUARANTEE",
    "guar_title":        "Don't love it — we'll refund",
    "guar_body":         'If the finished story doesn\'t land with your child — write to us at <a href="mailto:hello@lalaka.ai">hello@lalaka.ai</a> within 24 hours. We\'ll refund without questions, without bureaucracy.',
    "guar_small":        "The guarantee covers any reason: voice quality, illustrations, plot, even the voice timbre.",

    # 3 more FAQ
    "faq_q6":            "How does the voice sound — is it a robot?",
    "faq_a6":            "No. A real human-quality voice with natural emotions, pauses, and intonations — pleasant to listen to at bedtime. The 30-second sample above shows exactly how it sounds.",
    "faq_q7":            "How do I pay?",
    "faq_a7":            "By Visa / Mastercard / Maestro / American Express — payment goes through FastSpring (international merchant of record). Receipt is emailed to you immediately.",
    "faq_q8":            "What do I get in the end?",
    "faq_a8":            "An HD video with warm narration and 10–15 illustrations, plus a separate audio file — watch together on a big screen, listen on a phone at bedtime.",
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
        f"Translate the following Lalaka landing-page strings into {lang_name}. "
        "IMPORTANT: child names (Liam, Sofia, Mia, Lucas, Noah, Olivia, James, Lily, Lena) "
        f"MUST be replaced with culturally-appropriate names for {lang_name} speakers. "
        "Examples for ja: ゆうき, さくら, ひろし, あおい. For ar: ليلى, عمر, مريم, خالد. "
        "For de: Mila, Lukas, Felix. For ko: 보미, 민준, 지우. Keep ages.\n"
        "Preserve emojis, HTML tags, punctuation style. Return ONLY valid JSON.\n\n"
        f"INPUT:\n{json.dumps(EN_KEYS, ensure_ascii=False, indent=2)}\n\n"
        "Return ONLY the JSON."
    )
    body = {
        "model": "google/gemini-2.5-flash",
        "messages": [
            {"role": "system", "content": f"You are an expert {lang_name} marketing copy translator for children's products."},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 6000, "temperature": 0.2,
    }
    async with aiohttp.ClientSession() as s:
        async with s.post(OPENROUTER, json=body, headers={"Authorization": f"Bearer {api}"},
                          timeout=aiohttp.ClientTimeout(total=240)) as r:
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
    for k, v in EN_KEYS.items():
        data[k] = v
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logger.info(f"  en: +{len(EN_KEYS)}")

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
        logger.info(f"  {loc}: ok")


if __name__ == "__main__":
    asyncio.run(main())
