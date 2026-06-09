#!/usr/bin/env python3
"""Add order_read_more / order_read_less keys for 11 locales."""
import asyncio, json, os
from pathlib import Path
import aiohttp

OUT = Path("/app/web/locales")
LOCALES = ["en","de","es","fr","it","pl","pt-BR","tr","ja","ko","ar"]
EN_KEYS = {
    "order_read_more": "Read full story",
    "order_read_less": "Collapse",
}
LANG_NAMES = {"de":"German","es":"Spanish","fr":"French","it":"Italian","pl":"Polish",
              "pt-BR":"Brazilian Portuguese","tr":"Turkish","ja":"Japanese",
              "ko":"Korean","ar":"Modern Standard Arabic"}

async def translate(lang_name):
    api = os.environ["OPENROUTER_API_KEY"]
    p = (f"Translate to {lang_name}. Short button labels. Return ONLY JSON.\n"
         f"INPUT:\n{json.dumps(EN_KEYS, ensure_ascii=False, indent=2)}")
    body = {"model":"google/gemini-2.5-flash",
            "messages":[{"role":"system","content":f"Expert {lang_name} translator."},
                        {"role":"user","content":p}],
            "max_tokens":300,"temperature":0.15}
    async with aiohttp.ClientSession() as s:
        async with s.post("https://openrouter.ai/api/v1/chat/completions", json=body,
                          headers={"Authorization": f"Bearer {api}"},
                          timeout=aiohttp.ClientTimeout(total=60)) as r:
            d = await r.json()
    c = d["choices"][0]["message"]["content"].strip()
    if c.startswith("```"):
        c = c.split("```",2)[1]
        if c.startswith("json"): c = c[4:]
        c = c.strip("` \n")
    return json.loads(c)

async def main():
    p = OUT / "en.json"; data = json.loads(p.read_text(encoding="utf-8"))
    for k,v in EN_KEYS.items(): data[k] = v
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("  en: ok")
    for loc in LOCALES:
        if loc == "en": continue
        try: t = await translate(LANG_NAMES[loc])
        except Exception as e: print(f"  {loc}: ERR {e}"); continue
        p = OUT / f"{loc}.json"; data = json.loads(p.read_text(encoding="utf-8"))
        for k in EN_KEYS: data[k] = t.get(k) or EN_KEYS[k]
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"  {loc}: ok")

if __name__ == "__main__": asyncio.run(main())
