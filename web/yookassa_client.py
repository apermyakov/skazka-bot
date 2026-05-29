# -*- coding: utf-8 -*-
"""Minimal YuKassa (ЮKassa) API client. Creds from env (YUKASSA_SHOP_ID/SECRET_KEY).
Test store now; swap creds to live when the skazik.app store is activated."""
import base64
import os
import uuid

import aiohttp

API = "https://api.yookassa.ru/v3"


def _headers(idempotent: bool = True) -> dict:
    sid = os.environ.get("YUKASSA_SHOP_ID", "")
    sk = os.environ.get("YUKASSA_SECRET_KEY", "")
    h = {
        "Authorization": "Basic " + base64.b64encode(f"{sid}:{sk}".encode()).decode(),
        "Content-Type": "application/json",
    }
    if idempotent:
        h["Idempotence-Key"] = uuid.uuid4().hex
    return h


async def create_payment(amount_rub, description: str, return_url: str,
                         metadata: dict, receipt: dict | None = None) -> dict:
    body = {
        "amount": {"value": f"{float(amount_rub):.2f}", "currency": "RUB"},
        "capture": True,
        "confirmation": {"type": "redirect", "return_url": return_url},
        "description": description[:128],
        "metadata": metadata,
    }
    if receipt:
        body["receipt"] = receipt
    async with aiohttp.ClientSession() as s:
        async with s.post(f"{API}/payments", json=body, headers=_headers()) as r:
            return await r.json()


async def get_payment(payment_id: str) -> dict:
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{API}/payments/{payment_id}", headers=_headers(idempotent=False)) as r:
            return await r.json()


async def register_webhook(event: str, url: str) -> tuple[int, dict]:
    async with aiohttp.ClientSession() as s:
        async with s.post(f"{API}/webhooks", json={"event": event, "url": url}, headers=_headers()) as r:
            return r.status, await r.json()


async def list_webhooks() -> dict:
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{API}/webhooks", headers=_headers(idempotent=False)) as r:
            return await r.json()
