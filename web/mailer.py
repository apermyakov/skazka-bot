# -*- coding: utf-8 -*-
"""Outbound mail. If UNISENDER_API is set in env, use UniSender Go HTTP API
(better deliverability to yandex.ru/mail.ru). Otherwise fall back to SMTP."""
import asyncio
import json as _json
import logging
import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid

logger = logging.getLogger(__name__)

UNISENDER_API = os.environ.get("UNISENDER_API", "").strip()
UNISENDER_URL = "https://go1.unisender.ru/ru/transactional/api/v1/email/send.json"
EMAIL_FROM = os.environ.get("EMAIL_FROM", "papa@skazik.app")
EMAIL_FROM_NAME = os.environ.get("EMAIL_FROM_NAME") or os.environ.get("SMTP_FROM_NAME", "Сказик")


async def _send_via_unisender(to_addr: str, subject: str, body: str, html: str | None) -> bool:
    """Send via UniSender Go transactional API. Returns True on success."""
    import aiohttp
    payload = {
        "message": {
            "recipients": [{"email": to_addr}],
            "subject": subject,
            "from_email": EMAIL_FROM,
            "from_name": EMAIL_FROM_NAME,
            "body": {
                "plaintext": body,
                **({"html": html} if html else {}),
            },
            "track_links": 0,
            "track_read": 0,
        }
    }
    headers = {"X-API-KEY": UNISENDER_API, "Content-Type": "application/json"}
    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(UNISENDER_URL, json=payload, headers=headers) as r:
                txt = await r.text()
                try:
                    data = _json.loads(txt)
                except Exception:
                    data = {"raw": txt[:300]}
                if r.status == 200 and data.get("status") == "success":
                    job = (data.get("job_id") or "?")
                    logger.info("Email sent to %s via UniSender (job %s) subject=%r",
                                to_addr, job, subject[:60])
                    return True
                logger.error("UniSender rejected email to %s: HTTP %d %s",
                             to_addr, r.status, str(data)[:400])
                return False
    except Exception as e:
        logger.error("UniSender send failed to %s: %s", to_addr, e)
        return False


def _send_via_unisender_sync(to_addr: str, subject: str, body: str, html: str | None) -> bool:
    """Sync wrapper so existing thread-pool callers work unchanged."""
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            # We're inside an event loop (rare for queue worker, but safe path):
            # use a fresh loop in this thread.
            new_loop = asyncio.new_event_loop()
            try:
                return new_loop.run_until_complete(_send_via_unisender(to_addr, subject, body, html))
            finally:
                new_loop.close()
        return asyncio.run(_send_via_unisender(to_addr, subject, body, html))
    except Exception as e:
        logger.error("UniSender sync wrapper failed for %s: %s", to_addr, e)
        return False


def _send_sync(to_addr: str, subject: str, body: str, html: str | None = None) -> bool:
    # If UniSender Go is configured, prefer it — better deliverability to RU mailboxes.
    if UNISENDER_API:
        return _send_via_unisender_sync(to_addr, subject, body, html)
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
        '<a href="https://skazik.app/" style="text-decoration:none;display:inline-block">'
        '<img src="https://skazik.app/static/logo_horizontal.png" alt="Сказик" height="32" '
        'style="display:block;height:32px;width:auto"></a>'
        '</td></tr>'
        f'<tr><td style="padding:0 0 8px;font-size:20px;font-weight:800">{g}</td></tr>'
        '<tr><td style="padding:0 0 14px;color:#2b2350">Спасибо, что написали нам. Сообщение получено — ответим лично, обычно в течение дня.</td></tr>'
        f'<tr><td style="padding:14px 16px;border-left:3px solid #7c5cff;background:#f1ebff;color:#2b2350;border-radius:8px;white-space:pre-wrap">{m}</td></tr>'
        '<tr><td style="padding:14px 0 0;border-top:1px solid #ece6fb;color:#6b6390;font-size:13px">'
        'С теплом,<br>команда <a href="https://skazik.app/" style="color:#6b6390">Сказика</a>'
        '</td></tr>'
        '</table></td></tr></table></body></html>'
    )
    # Persist to queue first; worker picks it up. Survives container restart.
    from web.email_queue import enqueue_email
    await enqueue_email(to_addr, subject, body, html)
    return True


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


async def send_text_ready_invite(to_addr: str, title: str, order_url: str) -> bool:
    """Sent immediately when story TEXT is ready (before voice/illustrations).
    Acts as a tab-close safety net: user gets a link to come back and pay."""
    title = (title or "Сказка").strip()
    sample_url = "https://skazik.app/sample"
    subject = f"📝 «{title}» — текст готов, можно слушать"
    body = (
        f"Здравствуйте!\n\n"
        f"Мы написали для вас сказку «{title}». Текст уже на странице — "
        f"можно прочитать прямо сейчас и решить, превращать ли её в полноценную "
        f"аудиосказку с озвучкой профессиональным голосом, иллюстрациями и видео.\n\n"
        f"Открыть сказку: {order_url}\n"
        f"Послушать пример озвучки: {sample_url}\n\n"
        f"Гарантия: если результат не понравится — вернём деньги в течение суток. "
        f"Напишите нам через форму обратной связи: https://skazik.app/feedback\n\n"
        f"С теплом,\nкоманда Сказика\nhttps://skazik.app"
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
        f'<tr><td style="padding:0 0 6px;font-size:22px;font-weight:800;line-height:1.25">📝 Текст сказки готов</td></tr>'
        f'<tr><td style="padding:0 0 16px;color:#2b2350">«<b>{t}</b>» — можно открыть, прочитать и решить, '
        f'превращать ли в полноценную аудиосказку. Текст бесплатно навсегда.</td></tr>'
        '<tr><td style="padding:0 0 6px" align="center">'
        f'<a href="{_esc(order_url)}" '
        'style="display:inline-block;background:linear-gradient(135deg,#7c5cff,#ff7eb6);'
        'color:#ffffff;text-decoration:none;font-weight:800;font-size:17px;padding:14px 26px;'
        'border-radius:14px">✨ Открыть сказку</a></td></tr>'
        f'<tr><td style="padding:6px 0 16px" align="center">'
        f'<a href="{_esc(sample_url)}" style="color:#7c5cff;font-size:14px;text-decoration:none;font-weight:600">'
        f'🎧 Сначала послушать пример озвучки</a></td></tr>'
        '<tr><td style="padding:14px 16px;background:#f5efff;border-radius:12px;color:#3a2f6b;font-size:14px;line-height:1.55">'
        '<b>Что получите за 499 ₽ (стартовая цена до 30 июня):</b><br>🎙 Озвучка живым голосом · 🎨 4-6 тёплых иллюстраций '
        '· 🎬 HD-видео · 🛡️ Гарантия возврата</td></tr>'
        '<tr><td style="padding:14px 0 0;border-top:1px solid #ece6fb;color:#6b6390;font-size:13px">'
        'Текст сохранится по ссылке — можно вернуться когда удобно.</td></tr>'
        '<tr><td style="padding:10px 0 0;color:#6b6390;font-size:13px">'
        'С теплом,<br>команда <a href="https://skazik.app/" style="color:#6b6390">Сказика</a></td></tr>'
        '</table></td></tr></table></body></html>'
    )
    from web.email_queue import enqueue_email
    await enqueue_email(to_addr, subject, body, html)
    return True


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
        '<a href="https://skazik.app/" style="text-decoration:none;display:inline-block">'
        '<img src="https://skazik.app/static/logo_horizontal.png" alt="Сказик" height="32" '
        'style="display:block;height:32px;width:auto"></a>'
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
    # Persist to queue first; worker picks it up. Survives container restart.
    from web.email_queue import enqueue_email
    await enqueue_email(to_addr, subject, body, html)
    return True


async def send_followup_rating(to_addr: str, title: str, oid: str,
                                send_at=None) -> bool:
    """Schedule a 24h-after-delivery email asking «понравилось ли?».
    Email body has 5 direct rating links (?rate=1..5) that auto-submit on /order page.
    Worker skips this email if order is already rated before send_at fires.
    """
    title = (title or "Сказка").strip()
    order_url = f"https://skazik.app/order/{oid}"
    subject = f"Понравилась ли «{title}»?"
    body = (
        f"Здравствуйте!\n\n"
        f"Вчера мы прислали вам сказку «{title}». Понравилась ли она ребёнку?\n\n"
        f"Оцените одним кликом:\n"
        f"🤩 Супер — {order_url}?rate=5\n"
        f"😊 Хорошо — {order_url}?rate=4\n"
        f"🙂 Нормально — {order_url}?rate=3\n"
        f"😕 Так себе — {order_url}?rate=2\n"
        f"😔 Не понравилось — {order_url}?rate=1\n\n"
        f"Если не понравилось — мы переделаем бесплатно или вернём деньги. "
        f"Напишите через форму обратной связи: https://skazik.app/feedback\n\n"
        f"С теплом,\nкоманда Сказика"
    )
    t = _esc(title)
    def btn(n, label, bg):
        return (
            f'<a href="{order_url}?rate={n}" '
            f'style="display:inline-block;background:{bg};color:#ffffff;text-decoration:none;'
            f'font-weight:700;font-size:15px;padding:11px 18px;border-radius:12px;margin:4px">'
            f'{label}</a>'
        )
    stars_row = (
        btn(5, "🤩 Супер", "#1faa59")
        + btn(4, "😊 Хорошо", "#7c5cff")
        + btn(3, "🙂 Нормально", "#9b8fc2")
        + btn(2, "😕 Так себе", "#d97a4a")
        + btn(1, "😔 Не зашло", "#c0392b")
    )
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
        f'<tr><td style="padding:0 0 8px;font-size:22px;font-weight:800;line-height:1.25">'
        f'Понравилась ли «{t}»?</td></tr>'
        '<tr><td style="padding:0 0 18px;color:#6b6390;font-size:15px">'
        'Расскажите одним кликом — нам важно знать, угадали ли мы с настроением.</td></tr>'
        f'<tr><td align="center" style="padding:0 0 18px">{stars_row}</td></tr>'
        '<tr><td style="padding:14px 16px;background:#fff5d6;border-radius:12px;color:#7a5400;font-size:14px">'
        '<b>Если не зашло —</b> переделаем бесплатно или вернём деньги в течение суток. '
        'Напишите через <a href="https://skazik.app/feedback" style="color:#7a5400;font-weight:700">'
        'форму обратной связи</a>.</td></tr>'
        '<tr><td style="padding:14px 0 0;color:#6b6390;font-size:13px">'
        'С теплом,<br>команда <a href="https://skazik.app/" style="color:#6b6390">Сказика</a></td></tr>'
        '</table></td></tr></table></body></html>'
    )
    from web.email_queue import enqueue_email
    await enqueue_email(to_addr, subject, body, html,
                         send_at=send_at,
                         meta={"type": "followup_rating", "order_id": oid})
    return True
