"""Send refund-completed apology to Gelya (feedback #13 for order b164040a5b114f84).

Refund 499 RUB via SBP was processed at 2026-06-16 12:02 UTC.
Refund id: 31c34f15-0015-5000-b000-15c60c6bba6e
"""
import asyncio
import os
import sys

sys.path.insert(0, "/app")

import db.database as db_mod
from web.email_queue import enqueue_email


SUBJECT = "Деньги вернули — извините, что сказка не подошла"

BODY_TEXT = """\
Здравствуйте!

Спасибо за обратную связь. Я посмотрел вашу сказку — действительно, для двухлетки она получилась сложноватой. Вы просили «стих Гуси-гуси», а наша система написала большую сказку по мотивам потешки: с волком, который грустит, с уговорами, с подарком-камушком. Для четырёх-пяти лет это нормально, а для двух — длинно и слишком абстрактно. Винить тут некого, кроме нас: мы должны были честнее предупредить, что заточены под детей постарше.

499 рублей вернули на ту же карту через СБП. Деньги обычно приходят в течение 1-2 рабочих дней.

Если позже захотите ещё раз, когда Мелисса подрастёт — будем рады. А сейчас извините за неудачный опыт.

С теплом,
команда Сказика
papa@skazik.app
"""

BODY_HTML = """\
<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 16px; line-height: 1.55; color: #2d2d2d; max-width: 560px;">
<p>Здравствуйте!</p>

<p>Спасибо за обратную связь. Я посмотрел вашу сказку — действительно, для двухлетки она получилась сложноватой. Вы просили «стих Гуси-гуси», а наша система написала большую сказку по мотивам потешки: с волком, который грустит, с уговорами, с подарком-камушком. Для четырёх-пяти лет это нормально, а для двух — длинно и слишком абстрактно. Винить тут некого, кроме нас: мы должны были честнее предупредить, что заточены под детей постарше.</p>

<p><strong>499 рублей вернули на ту же карту через СБП.</strong> Деньги обычно приходят в течение 1-2 рабочих дней.</p>

<p>Если позже захотите ещё раз, когда Мелисса подрастёт — будем рады. А сейчас извините за неудачный опыт.</p>

<p>С теплом,<br>
команда Сказика<br>
<a href="mailto:papa@skazik.app">papa@skazik.app</a></p>
</div>
"""


async def main():
    await db_mod.init_db()
    eid = await enqueue_email(
        "gelya_abash@mail.ru",
        SUBJECT,
        BODY_TEXT,
        BODY_HTML,
        meta={"type": "refund_apology", "order_id": "b164040a5b114f84",
              "refund_id": "31c34f15-0015-5000-b000-15c60c6bba6e",
              "feedback_id": 13},
    )
    print(f"queued email row #{eid}")

    async with db_mod._pool.acquire() as c:
        await c.execute(
            "UPDATE feedback SET status='replied', replied_at=NOW() WHERE id=13"
        )
    print("feedback #13 marked replied")


if __name__ == "__main__":
    asyncio.run(main())
