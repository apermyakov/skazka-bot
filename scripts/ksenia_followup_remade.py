"""Follow-up to Ksenia (kseniya.kostyuk.74@mail.ru) after we remade all 3
paid stories with the new VLM-photo-priority + topic-aware pipeline.

Different from the earlier honest_reply (which said "our product doesn't match
your needs"): now we have something to deliver — links to the 3 corrected
videos. Tone: own the previous failure, explain root cause + fix at system
level, deliver new links, "we" form, end with Сказик-бот signature.
"""
import asyncio
import sys

sys.path.insert(0, "/app")

import db.database as db_mod
from web.email_queue import enqueue_email


SUBJECT = "Софиечка — переделанные сказки"

BODY_TEXT = """\
Здравствуйте, Ксения!

Мы внимательно перечитали все ваши сообщения и пересмотрели три ваши оплаченные сказки. Вы абсолютно правы: на иллюстрациях Софиечку рисовало совсем не так, как вы описывали, — путали цвет волос, рисовали Риччи не той породой, добавляли людей, которых нет в задании.

Мы разобрались, почему так вышло. Раньше наша система при создании картинок опиралась преимущественно на пересказанный текст сказки и не «дочитывала» ваше задание дословно. Из-за этого терялись важные детали — золотистые кудри, голубые глаза, конкретная порода Риччи (кинг чарльз спаниель), плюшевые Шарик и Заенька.

За эти несколько дней мы поправили это на уровне самой системы — теперь каждая иллюстрация сначала проверяется по вашему исходному заданию и по загруженному фото, а спорные кадры автоматически перерисовываются. Чтобы убедиться, что всё работает, мы заново отрисовали все три ваши сказки и пересобрали видео — текст и озвучка остались прежними, изменились только картинки.

Вот ссылки на новые версии:

1. Софийка в Королевстве Бабочек-Фей
https://skazik.app/media/0580231af4df/fairytale.mp4

2. Софиечка и Замок Детства
https://skazik.app/media/f6f9479adcc6/fairytale.mp4

3. Волшебный гномик и домики для игрушек
https://skazik.app/media/668b912ded17/fairytale.mp4

Если что-то в новых версиях вы хотели бы поправить ещё — напишите нам, мы посмотрим вместе. Спасибо вам за подробную обратную связь: благодаря ей наша система стала точнее для всех родителей, которые будут делать сказки после вас.

С уважением, Сказик-бот
(цифровой помощник команды Сказика)
"""

BODY_HTML = """\
<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 16px; line-height: 1.55; color: #2d2d2d; max-width: 600px;">
<p>Здравствуйте, Ксения!</p>

<p>Мы внимательно перечитали все ваши сообщения и пересмотрели три ваши оплаченные сказки. Вы абсолютно правы: на иллюстрациях Софиечку рисовало совсем не так, как вы описывали, — путали цвет волос, рисовали Риччи не той породой, добавляли людей, которых нет в задании.</p>

<p>Мы разобрались, почему так вышло. Раньше наша система при создании картинок опиралась преимущественно на пересказанный текст сказки и не «дочитывала» ваше задание дословно. Из-за этого терялись важные детали — золотистые кудри, голубые глаза, конкретная порода Риччи (кинг чарльз спаниель), плюшевые Шарик и Заенька.</p>

<p>За эти несколько дней мы поправили это <strong>на уровне самой системы</strong> — теперь каждая иллюстрация сначала проверяется по вашему исходному заданию и по загруженному фото, а спорные кадры автоматически перерисовываются. Чтобы убедиться, что всё работает, мы заново отрисовали все три ваши сказки и пересобрали видео — текст и озвучка остались прежними, изменились только картинки.</p>

<p>Вот ссылки на новые версии:</p>

<p><strong>1. Софийка в Королевстве Бабочек-Фей</strong><br>
<a href="https://skazik.app/media/0580231af4df/fairytale.mp4">https://skazik.app/media/0580231af4df/fairytale.mp4</a></p>

<p><strong>2. Софиечка и Замок Детства</strong><br>
<a href="https://skazik.app/media/f6f9479adcc6/fairytale.mp4">https://skazik.app/media/f6f9479adcc6/fairytale.mp4</a></p>

<p><strong>3. Волшебный гномик и домики для игрушек</strong><br>
<a href="https://skazik.app/media/668b912ded17/fairytale.mp4">https://skazik.app/media/668b912ded17/fairytale.mp4</a></p>

<p>Если что-то в новых версиях вы хотели бы поправить ещё — напишите нам, мы посмотрим вместе. Спасибо вам за подробную обратную связь: благодаря ей наша система стала точнее для всех родителей, которые будут делать сказки после вас.</p>

<p>С уважением, Сказик-бот<br>
<em style="color:#777">(цифровой помощник команды Сказика)</em></p>
</div>
"""


async def main():
    await db_mod.init_db()
    eid = await enqueue_email(
        "kseniya.kostyuk.74@mail.ru",
        SUBJECT,
        BODY_TEXT,
        BODY_HTML,
        meta={"type": "ksenia_followup_remade_2026_06_26",
              "orders": ["c9215044b1db4a5d",
                         "5a4142aee621498c",
                         "6b4929623a6b4aef"]},
    )
    print(f"queued email row #{eid}")

    async with db_mod._pool.acquire() as c:
        # any feedback rows that opened again get marked replied
        r1 = await c.execute(
            "UPDATE feedback SET status='replied', replied_at=NOW() "
            "WHERE email='kseniya.kostyuk.74@mail.ru' AND status='new'"
        )
        print(f"feedback: {r1}")
        r2 = await c.execute(
            "UPDATE inbox_messages SET status='archived' "
            "WHERE from_addr ILIKE '%kseniya.kostyuk.74@mail.ru%' AND status='new'"
        )
        print(f"inbox: {r2}")


if __name__ == "__main__":
    asyncio.run(main())
