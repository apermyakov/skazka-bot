# -*- coding: utf-8 -*-
"""Web order store (Postgres). One row per site order through its lifecycle:
composing → text_ready → paid → generating → done (or failed)."""
import json
import uuid

import db.database as db_mod

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS web_orders (
    id            TEXT PRIMARY KEY,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    topic         TEXT,
    photo_path    TEXT,
    status        TEXT DEFAULT 'composing',
    title         TEXT,
    story_text    TEXT,
    media_order_id TEXT,
    video_url     TEXT,
    audio_url     TEXT,
    illustrations JSONB,
    payment_id    TEXT,
    paid_at       TIMESTAMPTZ,
    error         TEXT
);
"""


FEEDBACK_SQL = """
CREATE TABLE IF NOT EXISTS feedback (
    id          SERIAL PRIMARY KEY,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    name        TEXT,
    email       TEXT,
    message     TEXT,
    status      TEXT DEFAULT 'new',
    replied_at  TIMESTAMPTZ
);
"""


async def init_orders():
    async with db_mod._pool.acquire() as c:
        await c.execute(CREATE_SQL)
        await c.execute("ALTER TABLE web_orders ADD COLUMN IF NOT EXISTS email TEXT")
        await c.execute("ALTER TABLE web_orders ADD COLUMN IF NOT EXISTS progress TEXT")
        await c.execute("ALTER TABLE web_orders ADD COLUMN IF NOT EXISTS utm_source TEXT")
        await c.execute("ALTER TABLE web_orders ADD COLUMN IF NOT EXISTS utm_medium TEXT")
        await c.execute("ALTER TABLE web_orders ADD COLUMN IF NOT EXISTS utm_campaign TEXT")
        await c.execute("ALTER TABLE web_orders ADD COLUMN IF NOT EXISTS utm_term TEXT")
        await c.execute("ALTER TABLE web_orders ADD COLUMN IF NOT EXISTS utm_content TEXT")
        await c.execute("ALTER TABLE web_orders ADD COLUMN IF NOT EXISTS referrer TEXT")
        await c.execute(FEEDBACK_SQL)


async def create_feedback(name: str | None, email: str | None, message: str) -> int:
    async with db_mod._pool.acquire() as c:
        row = await c.fetchrow(
            "INSERT INTO feedback (name, email, message) VALUES ($1,$2,$3) RETURNING id",
            name, email, message)
    return row["id"]


async def create_order(topic: str, photo_path: str | None = None, email: str | None = None,
                       utm: dict | None = None, referrer: str | None = None) -> str:
    oid = uuid.uuid4().hex[:16]
    utm = utm or {}
    async with db_mod._pool.acquire() as c:
        await c.execute(
            "INSERT INTO web_orders (id, topic, photo_path, email, "
            "utm_source, utm_medium, utm_campaign, utm_term, utm_content, referrer) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
            oid, topic, photo_path, email,
            utm.get("source"), utm.get("medium"), utm.get("campaign"),
            utm.get("term"), utm.get("content"), referrer)
    return oid


async def get_order(oid: str) -> dict | None:
    async with db_mod._pool.acquire() as c:
        row = await c.fetchrow("SELECT * FROM web_orders WHERE id=$1", oid)
    if not row:
        return None
    d = dict(row)
    if isinstance(d.get("illustrations"), str):
        try:
            d["illustrations"] = json.loads(d["illustrations"])
        except Exception:
            d["illustrations"] = None
    return d


async def claim_for_generation(oid: str, payment_id: str) -> bool:
    """Atomically move a paid order into 'generating'. Returns True only for the
    caller that actually performed the transition (race-safe between webhook and
    return-polling), so generation is launched exactly once."""
    async with db_mod._pool.acquire() as c:
        row = await c.fetchrow(
            "UPDATE web_orders SET status='generating', payment_id=$2, paid_at=NOW() "
            "WHERE id=$1 AND status IN ('awaiting_payment','text_ready','failed') "
            "RETURNING id", oid, payment_id)
    return row is not None


async def update_order(oid: str, **fields):
    if not fields:
        return
    vals = []
    sets = []
    for i, (k, v) in enumerate(fields.items(), start=2):
        sets.append(f"{k}=${i}")
        vals.append(json.dumps(v, ensure_ascii=False) if k == "illustrations" else v)
    async with db_mod._pool.acquire() as c:
        await c.execute(f"UPDATE web_orders SET {', '.join(sets)} WHERE id=$1", oid, *vals)
