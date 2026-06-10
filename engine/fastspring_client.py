# -*- coding: utf-8 -*-
"""FastSpring integration for Lalaka. Skazik continues to use YooKassa.

FastSpring serves the international market and handles VAT/sales tax for us
across EU/US/UK/etc. The flow we use is **Storefront URL** — simplest, no
JS embed required:

  1. User clicks "Pay $19.99" on /order/{oid}.
  2. We build a checkout URL pointing at our FastSpring store + product path,
     with the order id passed as a referrer tag and the buyer's email
     pre-filled.
  3. User completes payment on FastSpring-hosted page.
  4. FastSpring POSTs an `order.completed` event to /fastspring/webhook.
  5. We verify HMAC, look up the order by referrer (= our oid),
     mark status=generating, kick the pipeline.

ENV vars expected:
    FASTSPRING_STORE          — store identifier (e.g. "lalakaai" → lalakaai.onfastspring.com)
    FASTSPRING_PRODUCT_PATH   — product path/slug (e.g. "fairy-tale")
    FASTSPRING_WEBHOOK_SECRET — shared HMAC secret from dashboard
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
from typing import Optional
from urllib.parse import urlencode, quote

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    return bool(os.environ.get("FASTSPRING_STORE")) and bool(os.environ.get("FASTSPRING_PRODUCT_PATH"))


def build_checkout_url(oid: str, locale: str, email: Optional[str] = None,
                        return_url: Optional[str] = None) -> str:
    """Return a FastSpring-hosted storefront URL for a single order.

    `oid` is passed as the `referrer` query param so FastSpring echoes it
    back in the webhook payload — that's how we tie the payment to our
    order row. The 'tags' parameter additionally surfaces it under
    `data.tags.lalaka_oid` for redundancy.
    """
    store = os.environ["FASTSPRING_STORE"].strip()
    product = os.environ["FASTSPRING_PRODUCT_PATH"].strip().lstrip("/")
    base = f"https://{store}.onfastspring.com/{product}"
    params = {
        "referrer": oid,
        "tags": f"lalaka_oid={oid};locale={locale}",
    }
    if email:
        # FastSpring auto-fills the checkout email field from this hint.
        params["contact_email"] = email
    if return_url:
        params["thank_you_url"] = return_url
    return f"{base}?{urlencode(params, quote_via=quote)}"


def verify_webhook(raw_body: bytes, signature: str) -> bool:
    """Verify the X-FS-Signature header against the raw request body.

    FastSpring uses HMAC-SHA256 of the body, base64-encoded. If the secret
    isn't configured we refuse — silently letting unsigned events through
    would let anyone trigger generation for free.
    """
    secret = os.environ.get("FASTSPRING_WEBHOOK_SECRET", "").strip()
    if not secret or not signature:
        return False
    import base64
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
    expected_b64 = base64.b64encode(digest).decode("ascii")
    expected_hex = digest.hex()
    # FastSpring docs show both encodings depending on the dashboard era —
    # accept either constant-time.
    return hmac.compare_digest(signature, expected_b64) or hmac.compare_digest(signature, expected_hex)


def extract_oid_from_event(payload: dict) -> Optional[str]:
    """Pull our lalaka oid out of an order.completed event payload.

    Searches the documented locations in order: data.referrer,
    data.tags.lalaka_oid, then data.account.referrer."""
    data = payload.get("data") or {}
    oid = data.get("referrer")
    if oid:
        return str(oid)
    tags = data.get("tags") or {}
    if isinstance(tags, dict) and tags.get("lalaka_oid"):
        return str(tags["lalaka_oid"])
    acct = data.get("account") or {}
    if isinstance(acct, dict) and acct.get("referrer"):
        return str(acct["referrer"])
    return None
