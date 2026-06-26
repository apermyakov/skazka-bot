"""Smoke-test for check_image_compliance VLM.

Runs the QA on:
  - the OLD broken scene_9 (had "RULE #3" text + teacher face) → should flag adult+text
  - the OLD broken scene_2 (had baby sister face + small dog) → should flag other_child
  - the PATCHED scene_9 (Sofiechka alone with crafts) → should pass clean
  - a verified-good A scene → should pass clean
"""
import asyncio
import sys

sys.path.insert(0, "/app")


async def main():
    import db.database as db_mod
    from db.config_manager import cfg
    if db_mod._pool is None:
        await db_mod.init_db()
    cfg.set_pool(db_mod._pool)

    from engine.image_generator import check_image_compliance

    cases = [
        ("OLD B scene_9 (teacher + RULE #3 text)",
         "/app/media/f6f9479adcc6/illustrations_backup_1782454590/scene_9.png"),
        ("OLD B scene_2 (baby sister face)",
         "/app/media/f6f9479adcc6/illustrations_backup_1782454590/scene_2.png"),
        ("OLD B scene_6 (2 adult women holding hands)",
         "/app/media/f6f9479adcc6/illustrations_backup_1782454590/scene_6.png"),
        ("NEW B scene_9 (patched — Sofiechka alone with crafts)",
         "/app/media/f6f9479adcc6/illustrations/scene_9.png"),
        ("NEW A scene_1 (verified clean)",
         "/app/media/0580231af4df/illustrations/scene_1.png"),
    ]

    for label, path in cases:
        try:
            with open(path, "rb") as f:
                data = f.read()
        except FileNotFoundError:
            print(f"\n{label}: FILE NOT FOUND ({path})")
            continue
        result = await check_image_compliance(data, scene_index=-1)
        marker = "✅ OK" if result["ok"] else "❌ FAIL"
        print(f"\n{label}")
        print(f"  {marker} adult_present={result['adult_present']} "
              f"text={result['text_present']}")


if __name__ == "__main__":
    asyncio.run(main())
