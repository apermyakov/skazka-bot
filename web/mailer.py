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


async def send_email(to_addr: str, subject: str, body: str, html: str | None = None) -> bool:
    return await asyncio.to_thread(_send_sync, to_addr, subject, body, html)


async def send_story_ready(to_addr: str, title: str, order_url: str) -> bool:
    title = (title or "Сказка").strip()
    subject = f"«{title}» — ваша сказка готова"
    body = (
        f"Здравствуйте!\n\n"
        f"«{title}» готова к просмотру:\n{order_url}\n\n"
        f"На странице — видео, аудио и кнопки «Скачать».\n\n"
        f"— Сказик"
    )
    html = (
        f"<div style=\"font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:#2b2350;line-height:1.6\">"
        f"<p>Здравствуйте!</p>"
        f"<p><b>«{title}»</b> готова к просмотру:</p>"
        f"<p><a href=\"{order_url}\" style=\"color:#7c5cff;font-weight:700\">{order_url}</a></p>"
        f"<p>На странице — видео, аудио и кнопки «Скачать».</p>"
        f"<p style=\"color:#6b6390\">— Сказик</p>"
        f"</div>"
    )
    return await send_email(to_addr, subject, body, html)
