# -*- coding: utf-8 -*-
"""Resend-based email sender for Lalaka. Western provider so emails to
international users don't go through unisender.ru (which is Russia-based
and triggers spam filters in DE/US/JP mailers).

Resend (resend.com) is a modern transactional-email service:
- EU/US data residency
- Auto-configurable DKIM/SPF via the dashboard
- 3000 free emails/month, $20/mo for 50k
- Simple HTTPS POST API — no SMTP boilerplate

ENV vars expected:
    RESEND_API_KEY     — re_xxx — from https://resend.com/api-keys
    LALAKA_FROM_EMAIL  — verified sender, e.g. "hello@lalaka.ai"
    LALAKA_FROM_NAME   — display name, e.g. "Lalaka"

The lalaka_mailer module imports this provider directly instead of going
through web.email_queue (which routes to UniSender Go for skazik). We
still write a row to email_outbox for traceability + retry, but send via
Resend.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"


def _is_configured() -> bool:
    return bool(os.environ.get("RESEND_API_KEY")) and bool(os.environ.get("LALAKA_FROM_EMAIL"))


async def send_via_resend(to_addr: str, subject: str, body_text: str,
                           html: Optional[str] = None) -> bool:
    """Send a transactional email through Resend. Returns True on success.

    Raises nothing — logs and returns False on failure so the queue
    retry logic can decide what to do.
    """
    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    from_email = os.environ.get("LALAKA_FROM_EMAIL", "").strip()
    from_name = os.environ.get("LALAKA_FROM_NAME", "Lalaka").strip()

    if not api_key or not from_email:
        logger.error("Resend not configured (RESEND_API_KEY/LALAKA_FROM_EMAIL); skipping send to %s",
                     to_addr)
        return False

    payload = {
        "from": f"{from_name} <{from_email}>",
        "to": [to_addr],
        "subject": subject,
        "text": body_text,
    }
    if html:
        payload["html"] = html

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    timeout = aiohttp.ClientTimeout(total=30)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(RESEND_URL, json=payload, headers=headers) as r:
                text = await r.text()
                if r.status >= 200 and r.status < 300:
                    logger.info("Resend ✓ %s → %s (subject=%r)", r.status, to_addr, subject[:60])
                    return True
                logger.warning("Resend ✗ %s → %s body=%s", r.status, to_addr, text[:200])
                return False
    except Exception as e:
        logger.warning("Resend exception sending to %s: %s", to_addr, e)
        return False
