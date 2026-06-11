#!/usr/bin/env python3
"""Apology email to Diana's mother (order 65ed28135af14e7e).

DOES NOT send by default — prints the rendered HTML + plain text to stdout.
Pass --send to actually deliver via the existing skazik mailer (UniSender Go).
"""
from __future__ import annotations
import argparse, asyncio, sys
sys.path.insert(0, "/app")

TO_ADDR = "strekoza_001_@mail.ru"
ORDER_ID = "65ed28135af14e7e"
ORDER_URL = f"https://skazik.app/order/{ORDER_ID}"
VIDEO_URL = "https://skazik.app/media/87ba40a3fd8f/fairytale.mp4"

SUBJECT = "Поправили сказку про Диану — извините, что не сразу"

PLAIN = f"""Здравствуйте!

Спасибо большое, что написали — вы помогли нам поймать редкий случай: генератор иллюстраций "промахнулся" в двух кадрах из четырнадцати и нарисовал медведя вместо голубого дракона, а в другой сцене продублировал Диану через отражение в окне (отсюда впечатление "трёх рук"). В тексте сказки всё было правильно — дракон с золотистыми крылышками, как вы и просили в правке — баг был только в этих двух иллюстрациях.

Я перерисовал обе сцены и пересобрал видео. Теперь и там, где Диана прячется под одеялом, и там, где светлячок показывает ей тени от ветки за окном, всё именно так, как должно было быть с самого начала.

Обновлённая сказка по вашей старой ссылке (она не меняется):
{ORDER_URL}

Если хочется скачать видео напрямую:
{VIDEO_URL}

Ещё раз простите за неловкость, и приятного просмотра!

С теплом,
команда Сказик
https://skazik.app
"""

HTML = f"""<!DOCTYPE html><html><body style="margin:0;padding:0;background:#fbf7ff;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:#2b2350;line-height:1.6">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#fbf7ff;padding:24px 12px"><tr><td align="center">
<table width="560" cellpadding="0" cellspacing="0" border="0" style="max-width:560px;background:#ffffff;border-radius:18px;padding:28px 26px;box-shadow:0 6px 22px rgba(43,35,80,.06)">
<tr><td style="padding:0 0 18px"><img src="https://skazik.app/static/logo_horizontal.png" alt="Сказик" height="32" style="display:block"></td></tr>
<tr><td style="padding:0 0 14px;font-size:20px;font-weight:800;line-height:1.3">Поправили сказку — извините, что не сразу</td></tr>
<tr><td style="padding:0 0 14px;font-size:15px">Здравствуйте!</td></tr>
<tr><td style="padding:0 0 14px;font-size:15px">
Спасибо, что написали — вы помогли поймать редкий случай: генератор иллюстраций «промахнулся» в двух кадрах из четырнадцати и нарисовал медведя вместо голубого дракона, а в другой сцене продублировал Диану через отражение в окне (отсюда впечатление «трёх рук»). <b>В тексте сказки всё было правильно</b> — дракон с золотистыми крылышками, как вы и просили в правке — баг был только в этих двух иллюстрациях.
</td></tr>
<tr><td style="padding:0 0 14px;font-size:15px">
Я <b>перерисовал обе сцены и пересобрал видео</b>. Теперь и там, где Диана прячется под одеялом, и там, где светлячок показывает тени от ветки за окном — всё именно так, как должно было быть с самого начала.
</td></tr>
<tr><td style="padding:8px 0 14px" align="center">
  <a href="{ORDER_URL}" style="display:inline-block;background:linear-gradient(135deg,#7c5cff,#ff7eb6);color:#fff;text-decoration:none;font-weight:800;font-size:17px;padding:14px 26px;border-radius:14px">Открыть обновлённую сказку</a>
</td></tr>
<tr><td style="padding:0 0 14px;font-size:13.5px;color:#6b6390;text-align:center">
Ссылка на видео осталась прежней, просто откройте по старой —<br>файл уже подменён на исправленный.
</td></tr>
<tr><td style="padding:18px 0 0;color:#6b6390;font-size:14px">
Ещё раз простите за неловкость, и приятного просмотра!
</td></tr>
<tr><td style="padding:6px 0 0;color:#6b6390;font-size:13.5px">С теплом,<br>команда <b>Сказик</b></td></tr>
</table></td></tr></table></body></html>
"""

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true",
                    help="Actually send via skazik mailer (UniSender Go)")
    args = ap.parse_args()

    print("=" * 60)
    print(f"  To:      {TO_ADDR}")
    print(f"  Subject: {SUBJECT}")
    print("=" * 60)
    print()
    print(PLAIN)
    print("=" * 60)

    if not args.send:
        print("\n[dry-run] Pass --send to actually send via mailer.")
        return

    from web.email_queue import enqueue_email
    row_id = await enqueue_email(TO_ADDR, SUBJECT, PLAIN, HTML)
    print(f"\n✓ Queued as email_outbox row #{row_id} — worker will deliver shortly.")


if __name__ == "__main__":
    asyncio.run(main())
