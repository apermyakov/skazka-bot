# -*- coding: utf-8 -*-
"""Elastic Email transactional sender for Lalaka. We already use Elastic
for songria.com and getbutton.io — re-using the same provider here keeps
the operational stack consistent and avoids onboarding yet another vendor.

Endpoint: POST https://api.elasticemail.com/v4/emails
Auth: X-ElasticEmail-ApiKey header

ENV vars expected:
    ELASTIC_EMAIL_API_KEY  — from app.elasticemail.com → Settings → API
    LALAKA_FROM_EMAIL      — verified sender, e.g. "hello@lalaka.ai"
    LALAKA_FROM_NAME       — display name, e.g. "Lalaka"

The lalaka_mailer module imports this provider directly instead of going
through web.email_queue (which still routes to UniSender Go for skazik).
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

ELASTIC_URL = "https://api.elasticemail.com/v4/emails/transactional"


def _is_configured() -> bool:
    return bool(os.environ.get("ELASTIC_EMAIL_API_KEY")) and bool(os.environ.get("LALAKA_FROM_EMAIL"))


async def send_via_resend(to_addr: str, subject: str, body_text: str,
                           html: Optional[str] = None) -> bool:
    """Kept under the old name so callers don't break. Actually talks to
    Elastic Email's v4 transactional endpoint."""
    api_key = os.environ.get("ELASTIC_EMAIL_API_KEY", "").strip()
    from_email = os.environ.get("LALAKA_FROM_EMAIL", "").strip()
    from_name = os.environ.get("LALAKA_FROM_NAME", "Lalaka").strip()

    if not api_key or not from_email:
        logger.error("Elastic Email not configured; skipping send to %s", to_addr)
        return False

    content_parts = [{"ContentType": "PlainText", "Content": body_text, "Charset": "utf-8"}]
    if html:
        content_parts.append({"ContentType": "HTML", "Content": html, "Charset": "utf-8"})

    reply_to = os.environ.get("LALAKA_REPLY_TO", "").strip()
    content = {
        "From": f"{from_name} <{from_email}>",
        "Subject": subject,
        "Body": content_parts,
    }
    if reply_to:
        # noreply@ is the safer sender (some providers denylist hello@), but customer
        # replies should still reach a real inbox — set Reply-To to hello@lalaka.ai.
        content["ReplyTo"] = reply_to
    # /v4/emails/transactional expects Recipients as {"To": [...]} — different
    # schema from /v4/emails. The transactional endpoint bypasses the
    # marketing-consent contact status that was Bouncing our story-ready
    # emails for contacts opted into transactional-only (see 2026-06-25 fix).
    payload = {
        "Recipients": {"To": [to_addr]},
        "Content": content,
    }
    headers = {
        "X-ElasticEmail-ApiKey": api_key,
        "Content-Type": "application/json",
    }

    timeout = aiohttp.ClientTimeout(total=30)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(ELASTIC_URL, json=payload, headers=headers) as r:
                text = await r.text()
                if 200 <= r.status < 300:
                    logger.info("ElasticEmail ✓ %s → %s (subject=%r)", r.status, to_addr,
                                subject[:60])
                    return True
                logger.warning("ElasticEmail ✗ %s → %s body=%s", r.status, to_addr, text[:300])
                return False
    except Exception as e:
        logger.warning("ElasticEmail exception sending to %s: %s", to_addr, e)
        return False
