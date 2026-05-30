# -*- coding: utf-8 -*-
"""Outbound mail via Yandex SMTP. Sync smtplib in a thread (no extra dep).
Configured via SMTP_HOST/PORT/USER/PASS/FROM_NAME in env (env_file: .env)."""
import asyncio
import logging
import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid

logger = logging.getLogger(__name__)


def _send_sync(to_addr: str, subject: str, body: str, html: str | None = None) -> bool:
    host = os.environ.get("SMTP_HOST", "smtp.yandex.ru")
    port = int(os.environ.get("SMTP_PORT", "465"))
    user = os.environ.get("SMTP_USER", "")
    pwd = os.environ.get("SMTP_PASS", "")
    from_name = os.environ.get("SMTP_FROM_NAME", "Сказик")
    if not user or not pwd:
        logger.warning("SMTP creds not set — skipping email to %s", to_addr)
        return False
    if html:
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(body, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))
    else:
        msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = formataddr((from_name, user))
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid("skazik.app")
    try:
        with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=20) as s:
            s.login(user, pwd)
            s.sendmail(user, [to_addr], msg.as_string())
        logger.info("Email sent to %s subject=%r", to_addr, subject)
        return True
    except Exception as e:
        logger.error("Email send failed to %s: %s", to_addr, e)
        return False


async def send_email(to_addr: str, subject: str, body: str, html: str | None = None,
                     retries: int = 3) -> bool:
    """Send with exponential backoff. Logs at WARNING per failed attempt, ERROR if all fail."""
    delay = 5.0  # seconds; doubles each retry → 5s, 10s, 20s
    last = False
    for attempt in range(1, retries + 1):
        last = await asyncio.to_thread(_send_sync, to_addr, subject, body, html)
        if last:
            if attempt > 1:
                logger.info("email succeeded on attempt %d for %s", attempt, to_addr)
            return True
        if attempt < retries:
            logger.warning("email attempt %d/%d failed for %s — retrying in %.0fs",
                            attempt, retries, to_addr, delay)
            await asyncio.sleep(delay)
            delay *= 2
    logger.error("email gave up after %d attempts for %s", retries, to_addr)
    return False


async def send_feedback_ack(to_addr: str, name: str, message: str) -> bool:
    name = (name or "").strip()
    greet = f"Здравствуйте, {name}!" if name else "Здравствуйте!"
    subject = "Ваше сообщение получено — Сказик"
    body = (
        f"{greet}\n\n"
        f"Спасибо, что написали нам. Сообщение получено — ответим лично, обычно в течение дня.\n\n"
        f"Вы написали:\n«{(message or '').strip()[:1000]}»\n\n"
        f"С теплом,\nкоманда Сказика\nhttps://skazik.app"
    )
    g = _esc(greet)
    m = _esc((message or "").strip()[:1000])
    html = (
        '<!DOCTYPE html><html><head><meta charset="utf-8"></head>'
        '<body style="margin:0;padding:0;background:#fbf7ff;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:#2b2350;line-height:1.55">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#fbf7ff;padding:24px 12px">'
        '<tr><td align="center"><table role="presentation" width="560" cellpadding="0" cellspacing="0" border="0" '
        'style="max-width:560px;background:#ffffff;border-radius:18px;padding:28px 26px;box-shadow:0 6px 22px rgba(43,35,80,.06)">'
        '<tr><td style="padding:0 0 14px">'
        '<a href="https://skazik.app/" style="text-decoration:none;color:#2b2350;font-weight:800;font-size:22px">✨ Сказ<span style="color:#7c5cff">ик</span></a>'
        '</td></tr>'
        f'<tr><td style="padding:0 0 8px;font-size:20px;font-weight:800">{g}</td></tr>'
        '<tr><td style="padding:0 0 14px;color:#2b2350">Спасибо, что написали нам. Сообщение получено — ответим лично, обычно в течение дня.</td></tr>'
        f'<tr><td style="padding:14px 16px;border-left:3px solid #7c5cff;background:#f1ebff;color:#2b2350;border-radius:8px;white-space:pre-wrap">{m}</td></tr>'
        '<tr><td style="padding:14px 0 0;border-top:1px solid #ece6fb;color:#6b6390;font-size:13px">'
        'С теплом,<br>команда <a href="https://skazik.app/" style="color:#6b6390">Сказика</a>'
        '</td></tr>'
        '</table></td></tr></table></body></html>'
    )
    return await send_email(to_addr, subject, body, html)


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


async def send_story_ready(
    to_addr: str, title: str, order_url: str, cover_url: str | None = None
) -> bool:
    title = (title or "Сказка").strip()
    subject = f"«{title}» — ваша сказка готова"
    body = (
        f"Здравствуйте!\n\n"
        f"«{title}» готова к просмотру:\n{order_url}\n\n"
        f"На странице — видео, аудио и кнопки «Скачать».\n\n"
        f"С теплом,\nкоманда Сказика\nhttps://skazik.app"
    )
    t = _esc(title)
    cover_block = ""
    if cover_url:
        cover_block = (
            f'<tr><td style="padding:0 0 16px"><img src="{_esc(cover_url)}" alt="Иллюстрация" '
            f'style="display:block;width:100%;max-width:560px;border-radius:14px"></td></tr>'
        )
    html = (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        f'<title>{t}</title></head>'
        '<body style="margin:0;padding:0;background:#fbf7ff;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:#2b2350;line-height:1.55">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#fbf7ff;padding:24px 12px">'
        '<tr><td align="center">'
        '<table role="presentation" width="560" cellpadding="0" cellspacing="0" border="0" '
        'style="max-width:560px;background:#ffffff;border-radius:18px;padding:28px 26px;box-shadow:0 6px 22px rgba(43,35,80,.06)">'
        '<tr><td style="padding:0 0 14px">'
        '<a href="https://skazik.app/" style="text-decoration:none;color:#2b2350;font-weight:800;font-size:22px">✨ Сказ<span style="color:#7c5cff">ик</span></a>'
        '</td></tr>'
        f'<tr><td style="padding:0 0 8px;font-size:24px;font-weight:800;line-height:1.25">🎉 «{t}» готова!</td></tr>'
        '<tr><td style="padding:0 0 18px;color:#6b6390;font-size:15px">Видео, аудио и иллюстрации уже ждут на странице сказки.</td></tr>'
        f'{cover_block}'
        '<tr><td style="padding:0 0 18px" align="center">'
        f'<a href="{_esc(order_url)}" '
        'style="display:inline-block;background:linear-gradient(135deg,#7c5cff,#ff7eb6);color:#ffffff;'
        'text-decoration:none;font-weight:800;font-size:17px;padding:14px 26px;border-radius:14px">'
        '🎬 Открыть сказку</a></td></tr>'
        '<tr><td style="padding:0 0 6px;color:#6b6390;font-size:13px">Или ссылка:</td></tr>'
        f'<tr><td style="padding:0 0 18px;word-break:break-all"><a href="{_esc(order_url)}" '
        f'style="color:#7c5cff">{_esc(order_url)}</a></td></tr>'
        '<tr><td style="padding:14px 0 0;border-top:1px solid #ece6fb;color:#6b6390;font-size:13px">'
        'Можно скачать видео/аудио прямо на телефон — удобно слушать на ночь без интернета.'
        '</td></tr>'
        '<tr><td style="padding:10px 0 0;color:#6b6390;font-size:13px">'
        'С теплом,<br>команда <a href="https://skazik.app/" style="color:#6b6390">Сказика</a>'
        '</td></tr>'
        '</table></td></tr></table></body></html>'
    )
    return await send_email(to_addr, subject, body, html)
