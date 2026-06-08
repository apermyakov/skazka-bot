#!/usr/bin/env python3
"""Add /create-parity keys: create_example, create_trust_a, create_trust_b.

Skazik's /create has:
  .ex  — "Имя ребёнка (можно с фамилией), возраст, тема — например: «Сказка про Машу Петрову, 4 года, которая боится темноты»"
  .create-trust — "📝 Текст напишем бесплатно · 💜 Озвучивать или нет — решите потом"

Translate to all 11 locales via OpenRouter Gemini Flash, warm parental register.
"""
import asyncio, json, os
from pathlib import Path
import aiohttp

OUT = Path("/app/web/locales")
LOCALES = ["en","de","es","fr","it","pl","pt-BR","tr","ja","ko","ar"]

EN_KEYS = {
    "create_example": "Tell us the child's name, age, and a topic — e.g. \"A story about Mia, 4 years old, who is afraid of the dark.\"",
    "create_trust_a": "📝 The text is free to write",
    "create_trust_b": "💜 Voice it later — or not",
}

LANG_NAMES = {
    "de":"German","es":"Spanish","fr":"French","it":"Italian","pl":"Polish",
    "pt-BR":"Brazilian Portuguese","tr":"Turkish","ja":"Japanese",
    "ko":"Korean","ar":"Modern Standard Arabic",
}

async def translate(lang_name: str):
    api = os.environ["OPENROUTER_API_KEY"]
    prompt = (
        f"Translate Lalaka /create page strings to {lang_name}. Preserve emoji. "
        f"Warm parental register. Use a culturally appropriate child name (NOT 'Mia') in create_example. "
        f"Return ONLY valid JSON with same keys.\n\nINPUT:\n{json.dumps(EN_KEYS, ensure_ascii=False, indent=2)}\n\n"
        "Return ONLY JSON, no markdown fences."
    )
    body = {
        "model": "google/gemini-2.5-flash",
        "messages": [
            {"role": "system", "content": f"You are an expert {lang_name} translator for parental product copy."},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 1200, "temperature": 0.2,
    }
    async with aiohttp.ClientSession() as s:
        async with s.post("https://openrouter.ai/api/v1/chat/completions",
                           json=body, headers={"Authorization": f"Bearer {api}"},
                           timeout=aiohttp.ClientTimeout(total=120)) as r:
            data = await r.json()
    content = data["choices"][0]["message"]["content"].strip()
    if content.startswith("```"):
        content = content.split("```", 2)[1]
        if content.startswith("json"): content = content[4:]
        content = content.strip("` \n")
    return json.loads(content)

async def main():
    p = OUT / "en.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    for k, v in EN_KEYS.items(): data[k] = v
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  en: +{len(EN_KEYS)} keys")

    for loc in LOCALES:
        if loc == "en": continue
        try:
            translated = await translate(LANG_NAMES[loc])
        except Exception as e:
            print(f"  {loc}: ERR {e}")
            continue
        p = OUT / f"{loc}.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        for k in EN_KEYS:
            data[k] = translated.get(k) or EN_KEYS[k]
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"  {loc}: ok ({list(translated.values())[0][:60]}...)")

if __name__ == "__main__":
    asyncio.run(main())
