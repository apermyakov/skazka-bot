"""Send the honest "we're an automated service, no manual illustrator" reply
to Ksenia (kseniya.kostyuk.74@mail.ru). NO refund mention — that's intentional;
we open the door for one if she follows up but don't volunteer money yet.

Marks her open feedback rows (17-34) as replied and archives the recent
inbox messages from her.
"""
import asyncio
import sys

sys.path.insert(0, "/app")

import db.database as db_mod
from web.email_queue import enqueue_email


SUBJECT = "Re: Ваши замечания по сказкам"

BODY_TEXT = """\
Здравствуйте, Ксения!

Прежде всего — извините, что отвечаем не сразу. Мы внимательно прочитали все ваши сообщения и хотим честно объяснить, что у нас за продукт.

Сказик — это полностью автоматический сервис: текст сказки пишет нейросеть, иллюстрации тоже рисует нейросеть, и мы их потом склеиваем в видео. У нас, к сожалению, нет иллюстратора, который мог бы вручную перерисовать конкретные детали — например, заменить породу собаки на «кинг чарльз спаниель», поправить длину волос или цвет глаз так, чтобы это держалось одинаково во всех 12 сценах.

Технически нейросеть рисует «по мотивам» текста: какие-то детали попадает, какие-то нет, и это плавает от сцены к сцене. Мы стараемся сделать общее впечатление тёплым и узнаваемым, но точную правку отдельных элементов — это уже ручная работа иллюстратора, чего у нас в продукте нет.

Мы понимаем, что вам хочется видеть конкретно ваших девочек, конкретную собачку, с точными деталями. Если эти детали для вас принципиальны — наш продукт пока вам не очень подходит. Признаём это честно.

Спасибо вам за подробные замечания — это помогает нам понимать, куда двигаться дальше.

С теплом,
команда Сказика
papa@skazik.app
"""

BODY_HTML = """\
<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 16px; line-height: 1.55; color: #2d2d2d; max-width: 580px;">
<p>Здравствуйте, Ксения!</p>

<p>Прежде всего — извините, что отвечаем не сразу. Мы внимательно прочитали все ваши сообщения и хотим честно объяснить, что у нас за продукт.</p>

<p>Сказик — это <strong>полностью автоматический сервис</strong>: текст сказки пишет нейросеть, иллюстрации тоже рисует нейросеть, и мы их потом склеиваем в видео. У нас, к сожалению, <strong>нет иллюстратора, который мог бы вручную перерисовать конкретные детали</strong> — например, заменить породу собаки на «кинг чарльз спаниель», поправить длину волос или цвет глаз так, чтобы это держалось одинаково во всех 12 сценах.</p>

<p>Технически нейросеть рисует «по мотивам» текста: какие-то детали попадает, какие-то нет, и это плавает от сцены к сцене. Мы стараемся сделать общее впечатление тёплым и узнаваемым, но точную правку отдельных элементов — это уже ручная работа иллюстратора, чего у нас в продукте нет.</p>

<p>Мы понимаем, что вам хочется видеть конкретно ваших девочек, конкретную собачку, с точными деталями. <strong>Если эти детали для вас принципиальны — наш продукт пока вам не очень подходит.</strong> Признаём это честно.</p>

<p>Спасибо вам за подробные замечания — это помогает нам понимать, куда двигаться дальше.</p>

<p>С теплом,<br>
команда Сказика<br>
<a href="mailto:papa@skazik.app">papa@skazik.app</a></p>
</div>
"""


async def main():
    await db_mod.init_db()
    eid = await enqueue_email(
        "kseniya.kostyuk.74@mail.ru",
        SUBJECT,
        BODY_TEXT,
        BODY_HTML,
        meta={"type": "ksenia_honest_explanation_2026_06_26",
              "feedback_ids": [17, 18, 20, 21, 22, 23, 24, 25, 27, 28, 30, 31, 32, 33, 34],
              "inbox_ids": [64332, 64657, 64822, 75048, 75193, 77770]},
    )
    print(f"queued email row #{eid}")

    async with db_mod._pool.acquire() as c:
        # mark all of Ksenia's open feedback as replied
        await c.execute(
            "UPDATE feedback SET status='replied', replied_at=NOW() "
            "WHERE email='kseniya.kostyuk.74@mail.ru' AND status='new'"
        )
        # archive her open inbox messages
        await c.execute(
            "UPDATE inbox_messages SET status='archived' "
            "WHERE from_addr ILIKE '%kseniya.kostyuk.74@mail.ru%' AND status='new'"
        )
    print("feedback + inbox marked done")


if __name__ == "__main__":
    asyncio.run(main())
