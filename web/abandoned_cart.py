# -*- coding: utf-8 -*-
"""Abandoned-cart worker: nudge users who reached payment but didn't pay.

- 1 hour after awaiting_payment: send a 'забыли оплатить?' email
  (only once, only if email present)
- 24 hours after awaiting_payment without paying: mark as 'abandoned'
"""
import asyncio
import logging

import db.database as db_mod

logger = logging.getLogger(__name__)

CREATE_COL_SQL = """
ALTER TABLE web_orders ADD COLUMN IF NOT EXISTS reminder_sent_at TIMESTAMPTZ;
"""

SCAN_INTERVAL = 600  # 10 min


async def init_schema():
    async with db_mod._pool.acquire() as c:
        await c.execute(CREATE_COL_SQL)


async def _send_reminder(oid: str, email: str, title: str, public_base: str):
    from web.mailer import _esc
    from web.email_queue import enqueue_email
    title = (title or "Сказка").strip()
    order_url = f"{public_base}/order/{oid}"
    subject = f"Не забыли? «{title}» ждёт оплаты"
    body = (
        f"Здравствуйте!\n\n"
        f"Вы начали оформлять сказку «{title}», но не завершили оплату.\n"
        f"Если что-то пошло не так — можно попробовать ещё раз: {order_url}\n\n"
        f"Деньги списываются только если сказка вам понравится.\n\n"
        f"С теплом,\nкоманда Сказика"
    )
    t = _esc(title)
    html = (
        '<!DOCTYPE html><html><body style="margin:0;padding:0;background:#fbf7ff;'
        'font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:#2b2350;line-height:1.55">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="background:#fbf7ff;padding:24px 12px"><tr><td align="center">'
        '<table role="presentation" width="560" cellpadding="0" cellspacing="0" border="0" '
        'style="max-width:560px;background:#ffffff;border-radius:18px;padding:28px 26px;'
        'box-shadow:0 6px 22px rgba(43,35,80,.06)">'
        '<tr><td style="padding:0 0 14px"><a href="https://skazik.app/" style="text-decoration:none;display:inline-block">'
        '<img src="https://skazik.app/static/logo_horizontal.png" alt="Сказик" height="32" '
        'style="display:block;height:32px;width:auto"></a></td></tr>'
        f'<tr><td style="padding:0 0 8px;font-size:22px;font-weight:800">Вы не закончили оплату 💜</td></tr>'
        f'<tr><td style="padding:0 0 14px;color:#2b2350">Сказка <b>«{t}»</b> почти готова — осталось только оплатить, и через 5 минут пришлём готовое видео и аудио.</td></tr>'
        '<tr><td style="padding:0 0 18px" align="center">'
        f'<a href="{_esc(order_url)}" style="display:inline-block;background:linear-gradient(135deg,#7c5cff,#ff7eb6);color:#ffffff;'
        'text-decoration:none;font-weight:800;font-size:17px;padding:14px 26px;border-radius:14px">'
        '💜 Завершить оплату</a></td></tr>'
        '<tr><td style="padding:14px 0 0;border-top:1px solid #ece6fb;color:#6b6390;font-size:13px">'
        'Если что-то не работает — напишите нам через <a href="https://skazik.app/feedback" style="color:#7c5cff">форму обратной связи</a>.</td></tr>'
        '<tr><td style="padding:10px 0 0;color:#6b6390;font-size:13px">'
        'С теплом,<br>команда <a href="https://skazik.app/" style="color:#6b6390">Сказика</a></td></tr>'
        '</table></td></tr></table></body></html>'
    )
    await enqueue_email(email, subject, body, html)


async def _scan_once(public_base: str):
    async with db_mod._pool.acquire() as c:
        # 1) Send reminders to orders 1h+ old, with email, no reminder yet
        candidates = await c.fetch("""
            SELECT id, email, title FROM web_orders
            WHERE status='awaiting_payment'
              AND email IS NOT NULL AND email <> ''
              AND created_at < NOW() - INTERVAL '1 hour'
              AND reminder_sent_at IS NULL
            LIMIT 20
        """)
        for r in candidates:
            try:
                await _send_reminder(r["id"], r["email"], r["title"], public_base)
                await c.execute(
                    "UPDATE web_orders SET reminder_sent_at=NOW() WHERE id=$1", r["id"])
                logger.info("abandoned-cart reminder queued for order %s (%s)", r["id"], r["email"])
            except Exception as e:
                logger.warning("abandoned-cart reminder for %s failed: %s", r["id"], e)

        # 2) Mark as abandoned anything that's been awaiting_payment for 24h+
        marked = await c.execute("""
            UPDATE web_orders
            SET status='abandoned'
            WHERE status='awaiting_payment'
              AND created_at < NOW() - INTERVAL '24 hours'
        """)
        # marked is a string like 'UPDATE 3'
        if marked and marked.startswith("UPDATE "):
            n = int(marked.split()[1])
            if n:
                logger.info("abandoned-cart: marked %d orders as 'abandoned'", n)


async def _worker(public_base: str):
    logger.info("abandoned_cart worker started")
    while True:
        try:
            await _scan_once(public_base)
        except Exception as e:
            logger.warning("abandoned_cart worker error: %s", e, exc_info=True)
        await asyncio.sleep(SCAN_INTERVAL)


_task = None


def start_worker(public_base: str):
    global _task
    if _task and not _task.done():
        return
    _task = asyncio.create_task(_worker(public_base))
