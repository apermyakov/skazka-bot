"""Test the new split_into_scenes with topic + photo_analysis.
Uses Ksenia's actual Sofiechka order — the screenplay was already generated.
Compare new character_appearances vs the broken ones we got last time:
   OLD: «светло-русые короткие с хвостиком», «пудель»
   NEW expected: «длинные золотистые кудри», «кинг чарльз спаниель»
"""
import asyncio
import base64
import json
import sys

sys.path.insert(0, "/app")

PHOTO = "/app/media/_web_uploads/0448001900a74eacbfbe3c2da3915656.jpg"
TOPIC = (
    "Про Софиечку, которой исполнилось 3 годика и она готовится пойти первый раз в садик "
    "и быть там без мамы. У Софии есть маленькая ещё сестричка Мариечка, но она ещё не пойдёт в сад. "
    "Есть любимая собачка Риччи (щенок породы кинг чарльз спаниель) и любимый плюшевый Шарик. "
    "Софиечка с голубыми глазами, длинные кудрявые золотистые волосы."
)


async def main():
    import db.database as db_mod
    if db_mod._pool is None:
        await db_mod.init_db()

    # Fetch screenplay we got for Sofiechka's order from api_calls
    async with db_mod._pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT response_text FROM api_calls "
            "WHERE purpose='screenplay_convert' AND response_text ILIKE '%Софиечка и Замок Детства%' "
            "ORDER BY id DESC LIMIT 1")
    raw = row["response_text"].strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1].lstrip("json").strip()
    screenplay = json.loads(raw)
    print(f"Screenplay title: {screenplay['title']}")
    print(f"Characters: {[c['name'] for c in screenplay['characters'] if c['id']!='narrator']}")

    from engine.image_generator import analyze_child_photo, split_into_scenes
    with open(PHOTO, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")

    photo_desc = await analyze_child_photo(b64, topic=TOPIC)
    print(f"\nVLM описание ребёнка: {photo_desc}")

    scenes, char_appearances = await split_into_scenes(
        screenplay, topic=TOPIC, photo_analysis=photo_desc)

    print("\n=== Новые character_appearances ===")
    for name, desc in char_appearances.items():
        print(f"  {name}: {desc}")


if __name__ == "__main__":
    asyncio.run(main())
