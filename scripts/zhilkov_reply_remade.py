"""Reply to Mikhail Zhilkov (zhilkov.94@mail.ru) — his June 19 face-likeness
complaint about «Сказка про принцессу Есению» (order 4a8b4dacb9724c89).

We regenerated all illustrations with the new photo-priority pipeline
(+ the face-paint exclusion fix his case surfaced) and rebuilt the video
in place at the same URL. Own the 9-day delay, deliver the link,
"we" form, Сказик-бот signature.

Marks his feedback row + inbox message as handled.
"""
import asyncio
import sys

sys.path.insert(0, "/app")

import db.database as db_mod
from web.email_queue import enqueue_email


TO = "zhilkov.94@mail.ru"
SUBJECT = "Re: вопрос по сгенерированному видео — переделали сказку про Есению"

BODY_TEXT = """\
Здравствуйте, Михаил!

Извините, что отвечаем с задержкой — ваше сообщение потребовало не просто ответа, а доработки самой системы.

Вы были правы: Есения на иллюстрациях получилась непохожей — другой цвет и длина волос, другой возраст. Мы разобрались в причине: система рисовала персонажа по тексту сказки и недостаточно опиралась на загруженное фото. Мы исправили это на уровне движка — теперь внешность героя сверяется с фотографией перед отрисовкой каждой сцены.

Вашу сказку мы пересобрали заново с новой технологией — та же озвучка, новые иллюстрации:

«Сказка про принцессу Есению»
https://skazik.app/media/cec1527b8e12/fairytale.mp4

Посмотрите — теперь Есения со светлыми волосами и серыми глазами, как на фото, и одинаковая во всех сценах. Если что-то ещё захочется поправить — просто ответьте на это письмо.

Спасибо за терпение и за то, что написали: ваше замечание помогло сделать сервис точнее для всех.

С уважением, Сказик-бот
(цифровой помощник команды Сказика)
"""

BODY_HTML = """\
<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 16px; line-height: 1.55; color: #2d2d2d; max-width: 600px;">
<p>Здравствуйте, Михаил!</p>

<p>Извините, что отвечаем с задержкой — ваше сообщение потребовало не просто ответа, а доработки самой системы.</p>

<p>Вы были правы: Есения на иллюстрациях получилась непохожей — другой цвет и длина волос, другой возраст. Мы разобрались в причине: система рисовала персонажа по тексту сказки и недостаточно опиралась на загруженное фото. Мы исправили это <strong>на уровне движка</strong> — теперь внешность героя сверяется с фотографией перед отрисовкой каждой сцены.</p>

<p>Вашу сказку мы пересобрали заново с новой технологией — та же озвучка, новые иллюстрации:</p>

<p><strong>«Сказка про принцессу Есению»</strong><br>
<a href="https://skazik.app/media/cec1527b8e12/fairytale.mp4">https://skazik.app/media/cec1527b8e12/fairytale.mp4</a></p>

<p>Посмотрите — теперь Есения со светлыми волосами и серыми глазами, как на фото, и одинаковая во всех сценах. Если что-то ещё захочется поправить — просто ответьте на это письмо.</p>

<p>Спасибо за терпение и за то, что написали: ваше замечание помогло сделать сервис точнее для всех.</p>

<p>С уважением, Сказик-бот<br>
<em style="color:#777">(цифровой помощник команды Сказика)</em></p>
</div>
"""


async def main():
    await db_mod.init_db()
    eid = await enqueue_email(
        TO, SUBJECT, BODY_TEXT, BODY_HTML,
        meta={"type": "zhilkov_remade_2026_07_02",
              "order": "4a8b4dacb9724c89",
              "feedback_id": 15,
              "inbox_id": 60727},
    )
    print(f"queued email row #{eid}")

    async with db_mod._pool.acquire() as c:
        await c.execute(
            "UPDATE feedback SET status='replied', replied_at=NOW() WHERE id=15")
        await c.execute(
            "UPDATE inbox_messages SET status='archived' WHERE id=60727")
    print("feedback #15 replied, inbox #60727 archived")


if __name__ == "__main__":
    asyncio.run(main())
