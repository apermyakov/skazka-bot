"""Email Daria the raw PNG illustrations she asked for + offer free redo.

Feedback #14 / order 08b15686095f49d8. She gave the story 4★ and wrote in
the comment that the story is great but the child on photo doesn't look like
her own — she asked for the illustrations as separate files so she can edit
faces herself.
"""
import asyncio
import sys

sys.path.insert(0, "/app")

import db.database as db_mod
from web.email_queue import enqueue_email


SUBJECT = "Картинки в PNG — для Софии"

BODY_TEXT = """\
Здравствуйте, Дарья!

Спасибо большое за оценку и за слова про сказку — очень приятно. Извините, что лицо не получилось похожим: face-сходство пока наша слабая сторона, и над этим работаем.

Вот все 11 иллюстраций отдельными PNG-файлами, в одном архиве (~14 МБ):
https://skazik.app/media/02d3f88b046f/illustrations.zip

Если хотите — могу заново перерисовать иллюстрации, без доплаты. Если получится найти фото, где лицо крупнее и хорошо освещено (не в полупрофиль), сходство обычно лучше. Просто ответьте на это письмо с новым фото — переделаем и пришлём.

С теплом,
команда Сказика
papa@skazik.app
"""

BODY_HTML = """\
<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 16px; line-height: 1.55; color: #2d2d2d; max-width: 560px;">
<p>Здравствуйте, Дарья!</p>

<p>Спасибо большое за оценку и за слова про сказку — очень приятно. Извините, что лицо не получилось похожим: face-сходство пока наша слабая сторона, и над этим работаем.</p>

<p>Вот все 11 иллюстраций отдельными PNG-файлами, в одном архиве (~14 МБ):<br>
<a href="https://skazik.app/media/02d3f88b046f/illustrations.zip">https://skazik.app/media/02d3f88b046f/illustrations.zip</a></p>

<p>Если хотите — могу <strong>заново перерисовать иллюстрации, без доплаты</strong>. Если получится найти фото, где лицо крупнее и хорошо освещено (не в полупрофиль), сходство обычно лучше. Просто ответьте на это письмо с новым фото — переделаем и пришлём.</p>

<p>С теплом,<br>
команда Сказика<br>
<a href="mailto:papa@skazik.app">papa@skazik.app</a></p>
</div>
"""


async def main():
    await db_mod.init_db()
    eid = await enqueue_email(
        "daria.maranchak@yandex.ru",
        SUBJECT,
        BODY_TEXT,
        BODY_HTML,
        meta={"type": "illustrations_zip_response",
              "order_id": "08b15686095f49d8",
              "feedback_id": 14},
    )
    print(f"queued email row #{eid}")

    async with db_mod._pool.acquire() as c:
        await c.execute(
            "UPDATE feedback SET status='replied', replied_at=NOW() WHERE id=14"
        )
    print("feedback #14 marked replied")


if __name__ == "__main__":
    asyncio.run(main())
