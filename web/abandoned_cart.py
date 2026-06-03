# -*- coding: utf-8 -*-
"""Abandoned-cart worker: nudge users who dropped off mid-funnel.

Two stages of nudge, each fires at most once per order (reminder_sent_at):
- text_ready 6h+ old → "ещё думаете? сказка ждёт озвучки" (LATE follow-up;
  the instant text_ready email goes from _compose right after generation).
- awaiting_payment 30+ min old → "забыли оплатить?"

Plus: awaiting_payment 24h+ → mark as 'abandoned'.
"""
import asyncio
import logging
import os

import db.database as db_mod

logger = logging.getLogger(__name__)

CREATE_COL_SQL = """
ALTER TABLE web_orders ADD COLUMN IF NOT EXISTS reminder_sent_at TIMESTAMPTZ;
"""

SCAN_INTERVAL = 600  # 10 min


async def init_schema():
    async with db_mod._pool.acquire() as c:
        await c.execute(CREATE_COL_SQL)


def _excluded_emails() -> list[str]:
    return [e.strip().lower() for e in
            os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()]


def _wrap_html(inner: str) -> str:
    return (
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
        f'{inner}'
        '<tr><td style="padding:14px 0 0;border-top:1px solid #ece6fb;color:#6b6390;font-size:13px">'
        'Если что-то не работает — напишите нам через <a href="https://skazik.app/feedback" style="color:#7c5cff">форму обратной связи</a>.</td></tr>'
        '<tr><td style="padding:10px 0 0;color:#6b6390;font-size:13px">'
        'С теплом,<br>команда <a href="https://skazik.app/" style="color:#6b6390">Сказика</a></td></tr>'
        '</table></td></tr></table></body></html>'
    )


def _cta_button(url: str, label: str) -> str:
    from web.mailer import _esc
    return (
        '<tr><td style="padding:0 0 18px" align="center">'
        f'<a href="{_esc(url)}" style="display:inline-block;background:linear-gradient(135deg,#7c5cff,#ff7eb6);'
        'color:#ffffff;text-decoration:none;font-weight:800;font-size:17px;padding:14px 26px;'
        f'border-radius:14px">{label}</a></td></tr>'
    )


async def _send_payment_reminder(oid: str, email: str, title: str, public_base: str):
    """Email for awaiting_payment: payment was started but not finished."""
    from web.mailer import _esc, _with_utm
    from web.email_queue import enqueue_email
    title = (title or "Сказка").strip()
    order_url = _with_utm(f"{public_base}/order/{oid}", "email_cart_payment")
    feedback_url = _with_utm(f"{public_base}/feedback", "email_cart_payment")
    subject = f"«{title}» — 1 шаг до сказки"
    body = (
        f"Здравствуйте!\n\n"
        f"Вы начали оформлять сказку «{title}», но оплата так и не прошла.\n"
        f"Если что-то пошло не так — попробуйте ещё раз: {order_url}\n\n"
        f"Гарантия: не понравится результат — вернём деньги в течение суток. "
        f"Напишите нам через форму обратной связи: {feedback_url}\n\n"
        f"С теплом,\nкоманда Сказика"
    )
    t = _esc(title)
    inner = (
        f'<tr><td style="padding:0 0 8px;font-size:22px;font-weight:800">Один шаг до сказки 💜</td></tr>'
        f'<tr><td style="padding:0 0 14px;color:#2b2350">Сказка <b>«{t}»</b> уже написана — '
        f'осталось только завершить оплату, и через 5–8 минут вы получите готовое видео '
        f'с озвучкой и 4-6 иллюстрациями.</td></tr>'
        + _cta_button(order_url, "💜 Завершить оплату")
        + f'<tr><td style="padding:8px 0 0;background:#eef9f1;border-radius:10px;padding:12px 14px;'
          f'color:#1f6f3c;font-size:13.5px;line-height:1.5">'
          f'<b>🛡️ Гарантия возврата.</b> Не понравится — вернём деньги в течение суток. '
          f'Напишите нам через <a href="{_esc(feedback_url)}" style="color:#1f6f3c;font-weight:700">форму обратной связи</a>.</td></tr>'
    )
    await enqueue_email(email, subject, body, _wrap_html(inner))


async def _send_text_ready_reminder(oid: str, email: str, title: str, public_base: str):
    """Email for text_ready: text generated, didn't proceed to 'Озвучить'."""
    from web.mailer import _esc, _with_utm
    from web.email_queue import enqueue_email
    title = (title or "Сказка").strip()
    order_url = _with_utm(f"{public_base}/order/{oid}", "email_cart_text_ready")
    sample_url = _with_utm(f"{public_base}/sample", "email_cart_text_ready")
    feedback_url = _with_utm(f"{public_base}/feedback", "email_cart_text_ready")
    subject = f"«{title}» — осталось одно нажатие"
    body = (
        f"Здравствуйте!\n\n"
        f"Мы написали для вас сказку «{title}». Осталось одно нажатие — "
        f"и через 5–8 минут вы получите готовое видео с озвучкой и иллюстрациями.\n\n"
        f"Открыть сказку: {order_url}\n"
        f"Послушать пример озвучки: {sample_url}\n\n"
        f"Гарантия: если результат не понравится — вернём деньги в течение суток. "
        f"Напишите нам через форму обратной связи: {feedback_url}\n\n"
        f"С теплом,\nкоманда Сказика"
    )
    t = _esc(title)
    inner = (
        f'<tr><td style="padding:0 0 8px;font-size:22px;font-weight:800">Ваша сказка ждёт озвучки ✨</td></tr>'
        f'<tr><td style="padding:0 0 14px;color:#2b2350">Текст <b>«{t}»</b> уже написан. '
        f'Осталось одно нажатие — и через 5–8 минут вы получите готовое видео с озвучкой '
        f'живым голосом и 4-6 тёплыми иллюстрациями.</td></tr>'
        + _cta_button(order_url, "✨ Открыть и озвучить")
        + f'<tr><td style="padding:6px 0 14px" align="center">'
          f'<a href="{_esc(sample_url)}" style="color:#7c5cff;font-size:14px;text-decoration:none;font-weight:600">'
          f'🎧 Сначала послушать пример</a></td></tr>'
        + f'<tr><td style="padding:8px 0 0;background:#eef9f1;border-radius:10px;padding:12px 14px;'
          f'color:#1f6f3c;font-size:13.5px;line-height:1.5">'
          f'<b>🛡️ Гарантия возврата.</b> Если результат не понравится — вернём деньги '
          f'в течение суток. Напишите нам через '
          f'<a href="{_esc(feedback_url)}" style="color:#1f6f3c;font-weight:700">форму обратной связи</a>.</td></tr>'
    )
    await enqueue_email(email, subject, body, _wrap_html(inner))


async def _scan_once(public_base: str):
    excluded = _excluded_emails()
    async with db_mod._pool.acquire() as c:
        # 1a) awaiting_payment 30+ min old → payment reminder
        pay_candidates = await c.fetch("""
            SELECT id, email, title FROM web_orders
            WHERE status='awaiting_payment'
              AND email IS NOT NULL AND email <> ''
              AND NOT (LOWER(email) = ANY($1::text[]))
              AND created_at < NOW() - INTERVAL '30 minutes'
              AND reminder_sent_at IS NULL
            LIMIT 20
        """, excluded)
        for r in pay_candidates:
            try:
                await _send_payment_reminder(r["id"], r["email"], r["title"], public_base)
                await c.execute(
                    "UPDATE web_orders SET reminder_sent_at=NOW() WHERE id=$1", r["id"])
                logger.info("abandoned-cart payment reminder queued for %s (%s)", r["id"], r["email"])
            except Exception as e:
                logger.warning("abandoned-cart payment reminder for %s failed: %s", r["id"], e)

        # 1b) text_ready 6+ hours old → follow-up nudge.
        # (Instant text_ready invite goes out from _compose; this is the LATE follow-up.)
        text_candidates = await c.fetch("""
            SELECT id, email, title FROM web_orders
            WHERE status='text_ready'
              AND email IS NOT NULL AND email <> ''
              AND NOT (LOWER(email) = ANY($1::text[]))
              AND created_at < NOW() - INTERVAL '6 hours'
              AND reminder_sent_at IS NULL
            LIMIT 20
        """, excluded)
        for r in text_candidates:
            try:
                await _send_text_ready_reminder(r["id"], r["email"], r["title"], public_base)
                await c.execute(
                    "UPDATE web_orders SET reminder_sent_at=NOW() WHERE id=$1", r["id"])
                logger.info("abandoned-cart text-ready reminder queued for %s (%s)", r["id"], r["email"])
            except Exception as e:
                logger.warning("abandoned-cart text-ready reminder for %s failed: %s", r["id"], e)

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
