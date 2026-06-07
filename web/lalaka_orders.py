# -*- coding: utf-8 -*-
"""Lalaka order store (Postgres). Mirrors web_orders shape but with locale +
currency + display amount. Skazik orders.py is intentionally untouched."""
import json
import uuid

import db.database as db_mod

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS lalaka_orders (
    id              TEXT PRIMARY KEY,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    locale          TEXT NOT NULL,
    topic           TEXT,
    photo_path      TEXT,
    email           TEXT,
    status          TEXT DEFAULT 'composing',
    title           TEXT,
    story_text      TEXT,
    media_order_id  TEXT,
    video_url       TEXT,
    audio_url       TEXT,
    illustrations   JSONB,
    payment_id      TEXT,
    paid_at         TIMESTAMPTZ,
    currency        TEXT,
    display_amount  NUMERIC(10,2),
    amount_rub      NUMERIC(10,2),
    progress        TEXT,
    utm_source      TEXT,
    utm_medium      TEXT,
    utm_campaign    TEXT,
    utm_term        TEXT,
    utm_content     TEXT,
    referrer        TEXT,
    ip              TEXT,
    user_agent      TEXT,
    rating          SMALLINT,
    rating_comment  TEXT,
    rated_at        TIMESTAMPTZ,
    followup_email_sent_at TIMESTAMPTZ,
    error           TEXT
);
"""


async def init_lalaka_orders():
    """Idempotent — safe to run on every startup."""
    async with db_mod._pool.acquire() as c:
        await c.execute(CREATE_SQL)
        await c.execute("CREATE INDEX IF NOT EXISTS idx_lalaka_orders_ip ON lalaka_orders(ip) WHERE ip IS NOT NULL")
        await c.execute("CREATE INDEX IF NOT EXISTS idx_lalaka_orders_locale ON lalaka_orders(locale)")
        await c.execute("CREATE INDEX IF NOT EXISTS idx_lalaka_orders_paid ON lalaka_orders(paid_at) WHERE paid_at IS NOT NULL")


async def create_order(locale: str, topic: str, email: str | None = None,
                       photo_path: str | None = None,
                       utm: dict | None = None, referrer: str | None = None,
                       ip: str | None = None, user_agent: str | None = None) -> str:
    oid = uuid.uuid4().hex[:16]
    utm = utm or {}
    async with db_mod._pool.acquire() as c:
        await c.execute(
            "INSERT INTO lalaka_orders (id, locale, topic, photo_path, email, "
            "utm_source, utm_medium, utm_campaign, utm_term, utm_content, referrer, ip, user_agent) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)",
            oid, locale, topic, photo_path, email,
            utm.get("source"), utm.get("medium"), utm.get("campaign"),
            utm.get("term"), utm.get("content"), referrer, ip, user_agent)
    return oid


async def get_order(oid: str) -> dict | None:
    async with db_mod._pool.acquire() as c:
        row = await c.fetchrow("SELECT * FROM lalaka_orders WHERE id=$1", oid)
    if not row:
        return None
    d = dict(row)
    if isinstance(d.get("illustrations"), str):
        try:
            d["illustrations"] = json.loads(d["illustrations"])
        except Exception:
            d["illustrations"] = None
    return d


async def update_order(oid: str, **fields):
    if not fields:
        return
    vals = []
    sets = []
    for i, (k, v) in enumerate(fields.items(), start=2):
        sets.append(f"{k}=${i}")
        vals.append(json.dumps(v, ensure_ascii=False) if k == "illustrations" else v)
    async with db_mod._pool.acquire() as c:
        await c.execute(f"UPDATE lalaka_orders SET {', '.join(sets)} WHERE id=$1", oid, *vals)


async def claim_for_generation(oid: str, payment_id: str) -> bool:
    """Atomically move a paid order into 'generating'. Race-safe between webhook and
    return-polling — generation is launched exactly once."""
    async with db_mod._pool.acquire() as c:
        row = await c.fetchrow(
            "UPDATE lalaka_orders SET status='generating', payment_id=$2, paid_at=NOW() "
            "WHERE id=$1 AND status IN ('awaiting_payment','text_ready','failed') "
            "RETURNING id", oid, payment_id)
    return row is not None


async def set_order_rating(oid: str, rating: int, comment: str | None) -> None:
    async with db_mod._pool.acquire() as c:
        await c.execute(
            "UPDATE lalaka_orders SET rating=$1, rating_comment=$2, rated_at=NOW() WHERE id=$3",
            rating, comment, oid)


async def mark_followup_sent(oid: str) -> None:
    async with db_mod._pool.acquire() as c:
        await c.execute("UPDATE lalaka_orders SET followup_email_sent_at=NOW() WHERE id=$1", oid)


async def count_orders_by_ip(ip: str) -> int:
    if not ip:
        return 0
    async with db_mod._pool.acquire() as c:
        row = await c.fetchrow("SELECT count(*) AS n FROM lalaka_orders WHERE ip=$1", ip)
    return int(row["n"]) if row else 0
