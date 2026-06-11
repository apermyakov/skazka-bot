# -*- coding: utf-8 -*-
"""IMAP poller that watches papa@skazik.app for incoming mail.

Connects to imap.yandex.ru via SSL once a minute, fetches everything that
isn't already in inbox_messages (dedup on Message-ID), and writes each new
mail into the DB along with a best-effort correlation to an existing
web_orders row or feedback entry.

Run as a long-lived background task started from web.app on lifespan
startup, so it shares the same DB pool and lifecycle as the rest of the
HTTP service.

ENV (skazik):
  IMAP_HOST       — defaults to imap.yandex.ru
  IMAP_PORT       — defaults to 993
  IMAP_USER       — full mailbox address, e.g. "papa@skazik.app"
  IMAP_PASS       — Yandex *application password* (not the account password)
  IMAP_POLL_SEC   — defaults to 60

Lalaka inbound (hello@lalaka.ai → permyakov@gmail.com via CF Email
Routing) already lands in the user's gmail; that flow doesn't need this
poller. This module is skazik-only.
"""
from __future__ import annotations

import asyncio
import email
import email.utils
import logging
import os
import re
from email.header import decode_header

import db.database as dbmod

logger = logging.getLogger(__name__)

POLL_INTERVAL = int(os.environ.get("IMAP_POLL_SEC", "60"))


def _decode(s) -> str:
    """Decode RFC2047 MIME-encoded headers ('=?utf-8?B?…?=') to plain str."""
    if s is None:
        return ""
    if isinstance(s, bytes):
        s = s.decode("utf-8", "replace")
    out = []
    for part, enc in decode_header(s):
        if isinstance(part, bytes):
            try:
                out.append(part.decode(enc or "utf-8", "replace"))
            except Exception:
                out.append(part.decode("utf-8", "replace"))
        else:
            out.append(part)
    return "".join(out).strip()


def _normalise_subject(s: str) -> str:
    """Strip Re:/Fwd: prefixes so a thread groups together."""
    s = (s or "").strip()
    while True:
        m = re.match(r"^(re|fw|fwd|ответ|пересл)[:\s\[\]]+", s, re.I)
        if not m:
            break
        s = s[m.end():]
    return s.strip().lower()


def _extract_text(msg: email.message.Message) -> tuple[str, str]:
    """Return (text/plain, text/html) bodies, both best-effort decoded."""
    text_body = ""
    html_body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = part.get("Content-Disposition") or ""
            if "attachment" in disp.lower():
                continue
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            try:
                txt = payload.decode(part.get_content_charset() or "utf-8", "replace")
            except Exception:
                txt = payload.decode("utf-8", "replace")
            if ctype == "text/plain" and not text_body:
                text_body = txt
            elif ctype == "text/html" and not html_body:
                html_body = txt
    else:
        payload = msg.get_payload(decode=True)
        if payload is not None:
            try:
                text_body = payload.decode(msg.get_content_charset() or "utf-8", "replace")
            except Exception:
                text_body = payload.decode("utf-8", "replace")
    return text_body[:200_000], html_body[:500_000]


async def _correlate(from_addr: str, subject_norm: str) -> tuple[str | None, int | None]:
    """Best-effort match to a web_orders row or feedback entry.

    Three signals, in priority order:
      1. The sender email maps to a paid order — easy direct hit.
      2. We sent a recent outbound mail with this subject; find the original
         recipient and look up THEIR paid order. This catches replies that
         come in from a different address (family member, alias) than the
         original purchase.
      3. Feedback row by sender email.
    """
    order_id, feedback_id = None, None
    async with dbmod._pool.acquire() as c:
        # Direct sender → paid order
        r = await c.fetchrow(
            "SELECT id FROM web_orders WHERE email=$1 AND paid_at IS NOT NULL "
            "ORDER BY paid_at DESC LIMIT 1", from_addr)
        if r:
            order_id = r["id"]

        # Subject-based recovery: a recently-sent outbound mail with this
        # normalised subject points us at the original buyer's address.
        if not order_id and subject_norm:
            o = await c.fetchrow(
                "SELECT to_addr FROM email_outbox "
                "WHERE LOWER(REGEXP_REPLACE(subject, '^(re|fw|fwd|ответ|пересл)[: \\[\\]]+', '', 'i')) = $1 "
                "  AND status='sent' "
                "  AND created_at > NOW() - INTERVAL '60 days' "
                "ORDER BY created_at DESC LIMIT 1",
                subject_norm)
            if o and o["to_addr"]:
                r = await c.fetchrow(
                    "SELECT id FROM web_orders WHERE email=$1 AND paid_at IS NOT NULL "
                    "ORDER BY paid_at DESC LIMIT 1", o["to_addr"])
                if r:
                    order_id = r["id"]

        # Feedback row from this address (independent signal)
        f = await c.fetchrow(
            "SELECT id FROM feedback WHERE email=$1 ORDER BY created_at DESC LIMIT 1",
            from_addr)
        if f:
            feedback_id = f["id"]
    return order_id, feedback_id


async def _store(msg: email.message.Message) -> bool:
    """Insert a single message into inbox_messages. Returns True if it was
    new (False on UPSERT-conflict which means we already had it)."""
    message_id = (_decode(msg.get("Message-ID")) or "").strip("<> \t\r\n")
    if not message_id:
        return False
    in_reply_to = (_decode(msg.get("In-Reply-To")) or "").strip("<> \t\r\n") or None
    refs = _decode(msg.get("References")) or None
    subject = _decode(msg.get("Subject"))
    from_raw = _decode(msg.get("From"))
    from_name, from_addr = email.utils.parseaddr(from_raw)
    to_addr = email.utils.parseaddr(_decode(msg.get("To")))[1]
    cc_addr = email.utils.parseaddr(_decode(msg.get("Cc")))[1] or None
    date_str = msg.get("Date")
    try:
        received_at = email.utils.parsedate_to_datetime(date_str) if date_str else None
    except Exception:
        received_at = None

    body_text, body_html = _extract_text(msg)
    thread_key = _normalise_subject(subject)
    order_id, feedback_id = await _correlate(from_addr.lower(), thread_key)

    async with dbmod._pool.acquire() as c:
        result = await c.execute(
            """INSERT INTO inbox_messages
                 (message_id, in_reply_to, "references", from_addr, from_name,
                  to_addr, cc_addr, subject, body_text, body_html, received_at,
                  order_id, feedback_id, thread_key)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,COALESCE($11,NOW()),$12,$13,$14)
               ON CONFLICT (message_id) DO NOTHING""",
            message_id, in_reply_to, refs,
            (from_addr or "").lower(), from_name or None,
            (to_addr or "").lower(), cc_addr,
            subject, body_text, body_html, received_at,
            order_id, feedback_id, thread_key,
        )
    return result == "INSERT 0 1"


async def _poll_once() -> int:
    """Run one IMAP fetch. Returns the number of new messages stored."""
    host = os.environ.get("IMAP_HOST", "imap.yandex.ru")
    port = int(os.environ.get("IMAP_PORT", "993"))
    user = os.environ.get("IMAP_USER", "")
    pwd  = os.environ.get("IMAP_PASS", "")
    if not user or not pwd:
        return 0

    # imaplib is sync — run in a thread so we don't block the event loop.
    import imaplib
    def _fetch_blocking():
        added = 0
        with imaplib.IMAP4_SSL(host, port) as imap:
            imap.login(user, pwd)
            imap.select("INBOX")
            # Pull everything from the last 7 days — UNSEEN was wrong because
            # if the operator opens the mail in Yandex web UI before our poll
            # tick fires, the message becomes SEEN and we'd miss it forever.
            # The unique index on message_id handles dedup so re-scanning a
            # week of mail every minute is cheap and correct.
            from datetime import datetime, timedelta
            since = (datetime.utcnow() - timedelta(days=7)).strftime("%d-%b-%Y")
            typ, data = imap.search(None, f"SINCE {since}")
            if typ != "OK" or not data or not data[0]:
                return 0, []
            uids = data[0].split()
            messages = []
            for uid in uids:
                typ, msg_data = imap.fetch(uid, "(RFC822)")
                if typ != "OK" or not msg_data:
                    continue
                raw = msg_data[0][1] if isinstance(msg_data[0], tuple) else msg_data[0]
                if not raw:
                    continue
                messages.append(email.message_from_bytes(raw))
            return len(uids), messages

    try:
        count, messages = await asyncio.to_thread(_fetch_blocking)
    except Exception as e:
        logger.warning("inbox poller fetch failed: %s", e)
        return 0

    added = 0
    for m in messages:
        try:
            if await _store(m):
                added += 1
        except Exception as e:
            logger.warning("inbox poller store failed: %s", e)
    if added:
        logger.info("inbox poller: %d new (of %d unseen)", added, count)
    return added


async def run_forever():
    """Long-running task. Started from web.app on lifespan startup."""
    if not os.environ.get("IMAP_USER") or not os.environ.get("IMAP_PASS"):
        logger.info("IMAP creds not set — inbox poller disabled")
        return
    logger.info("inbox poller started (every %ds)", POLL_INTERVAL)
    while True:
        try:
            await _poll_once()
        except Exception as e:
            logger.warning("inbox poller loop error: %s", e)
        await asyncio.sleep(POLL_INTERVAL)
