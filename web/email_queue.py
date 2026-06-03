# -*- coding: utf-8 -*-
"""Persistent email queue backed by Postgres. Survives container restarts.

Replaces fire-and-forget asyncio.create_task(send_email(...)) — those
got dropped if the container died mid-send. Now:

  1. Caller calls enqueue_email(...) → row inserted, status='queued'.
  2. Background worker (started on app lifespan startup) loops:
     - SELECT FOR UPDATE SKIP LOCKED 'queued' rows older than now
     - try send via mailer; on success → status='sent', sent_at=now
     - on failure → retries += 1; if retries < MAX, status='queued'
       again with next_try_at = now + backoff; else status='failed'

The mailer's own per-attempt 3x retry still runs *inside* one queue
attempt — but if the *process* dies, the row stays 'queued' and the
next startup picks it up.
"""
import asyncio
import json
import logging
import time
from datetime import datetime

import db.database as db_mod
from web.mailer import _send_sync

logger = logging.getLogger(__name__)

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS email_outbox (
    id           SERIAL PRIMARY KEY,
    to_addr      TEXT NOT NULL,
    subject      TEXT NOT NULL,
    body         TEXT NOT NULL,
    html         TEXT,
    status       TEXT NOT NULL DEFAULT 'queued',
    retries      INT  NOT NULL DEFAULT 0,
    last_error   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    next_try_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sent_at      TIMESTAMPTZ,
    meta         JSONB
);
CREATE INDEX IF NOT EXISTS email_outbox_due_idx
    ON email_outbox (next_try_at) WHERE status = 'queued';
"""

MAX_RETRIES = 5            # 5 queue-level attempts (each one runs the mailer's 3x SMTP retry inside)
BACKOFFS = [60, 300, 900, 1800, 3600]   # 1m, 5m, 15m, 30m, 1h
SCAN_INTERVAL = 20         # seconds between worker polls when idle


async def init_email_queue():
    async with db_mod._pool.acquire() as c:
        await c.execute(CREATE_SQL)
        # Idempotent migration for existing installations
        await c.execute("ALTER TABLE email_outbox ADD COLUMN IF NOT EXISTS meta JSONB")


async def enqueue_email(to_addr: str, subject: str, body: str, html: str | None = None,
                        send_at: datetime | None = None, meta: dict | None = None) -> int:
    """Insert one email into the queue. Returns the row id.

    send_at — UTC datetime to send after (uses NOW() if None — immediate).
    meta — JSON payload for worker-side guards (e.g. {'type': 'followup_rating', 'order_id': '…'}).
    """
    meta_json = json.dumps(meta) if meta else None
    async with db_mod._pool.acquire() as c:
        if send_at is not None:
            row = await c.fetchrow(
                "INSERT INTO email_outbox (to_addr, subject, body, html, next_try_at, meta) "
                "VALUES ($1,$2,$3,$4,$5,$6::jsonb) RETURNING id",
                to_addr, subject, body, html, send_at, meta_json)
        else:
            row = await c.fetchrow(
                "INSERT INTO email_outbox (to_addr, subject, body, html, meta) "
                "VALUES ($1,$2,$3,$4,$5::jsonb) RETURNING id",
                to_addr, subject, body, html, meta_json)
    logger.info("email queued #%s → %s subject=%r send_at=%s meta=%s",
                row["id"], to_addr, subject[:60], send_at, meta_json)
    return row["id"]


async def _should_skip(row) -> tuple[bool, str]:
    """Worker-side guard. For followup_rating: skip if order already rated.
    Returns (skip, reason).
    """
    meta = row.get("meta")
    if not meta:
        return False, ""
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            return False, ""
    if meta.get("type") == "followup_rating":
        oid = meta.get("order_id")
        if not oid:
            return False, ""
        async with db_mod._pool.acquire() as c:
            r = await c.fetchrow("SELECT rating FROM web_orders WHERE id=$1", oid)
        if r and r["rating"] is not None:
            return True, "already rated"
    return False, ""


async def _send_one(row) -> bool:
    """Returns True on success."""
    try:
        return await asyncio.to_thread(_send_sync, row["to_addr"], row["subject"],
                                        row["body"], row["html"])
    except Exception as e:
        logger.warning("queue email #%s sync failed: %s", row["id"], e)
        return False


async def _mark_sent(row_id: int):
    async with db_mod._pool.acquire() as c:
        await c.execute("UPDATE email_outbox SET status='sent', sent_at=NOW() WHERE id=$1", row_id)


async def _mark_retry(row_id: int, retries: int, err: str):
    next_in = BACKOFFS[min(retries - 1, len(BACKOFFS) - 1)]
    if retries >= MAX_RETRIES:
        async with db_mod._pool.acquire() as c:
            await c.execute(
                "UPDATE email_outbox SET status='failed', retries=$1, last_error=$2 WHERE id=$3",
                retries, err[:1000], row_id)
        logger.error("email #%s permanently failed after %d retries", row_id, retries)
    else:
        async with db_mod._pool.acquire() as c:
            await c.execute(
                "UPDATE email_outbox SET retries=$1, last_error=$2, "
                "next_try_at=NOW() + ($3 || ' seconds')::interval WHERE id=$4",
                retries, err[:1000], str(next_in), row_id)


async def _worker_loop():
    """Single background coroutine. Polls the queue; sends due rows; sleeps."""
    logger.info("email_queue worker started")
    while True:
        try:
            async with db_mod._pool.acquire() as c:
                # Race-safe pick: SKIP LOCKED so multiple workers don't double-send
                async with c.transaction():
                    row = await c.fetchrow(
                        "SELECT id, to_addr, subject, body, html, retries, meta FROM email_outbox "
                        "WHERE status='queued' AND next_try_at <= NOW() "
                        "ORDER BY next_try_at LIMIT 1 FOR UPDATE SKIP LOCKED")
                    if row:
                        # Mark as in-flight by setting next_try_at far in the future
                        # — prevents same row being re-picked while we send
                        await c.execute(
                            "UPDATE email_outbox SET next_try_at = NOW() + INTERVAL '5 minutes' "
                            "WHERE id=$1", row["id"])
            if row:
                # Pre-send guard — e.g. followup_rating skipped if already rated
                skip, reason = await _should_skip(row)
                if skip:
                    async with db_mod._pool.acquire() as c:
                        await c.execute("UPDATE email_outbox SET status='skipped', "
                                        "sent_at=NOW(), last_error=$2 WHERE id=$1",
                                        row["id"], reason)
                    logger.info("email #%s skipped (%s) → %s", row["id"], reason, row["to_addr"])
                    continue
                t0 = time.time()
                ok = await _send_one(row)
                dur = time.time() - t0
                if ok:
                    await _mark_sent(row["id"])
                    logger.info("email #%s sent to %s in %.1fs", row["id"], row["to_addr"], dur)
                else:
                    await _mark_retry(row["id"], (row["retries"] or 0) + 1,
                                       f"smtp send failed in {dur:.1f}s")
                # Loop again immediately if there might be more work
                continue
        except Exception as e:
            logger.warning("email_queue worker error: %s", e, exc_info=True)
        await asyncio.sleep(SCAN_INTERVAL)


_worker_task = None


def start_worker():
    global _worker_task
    if _worker_task and not _worker_task.done():
        return
    _worker_task = asyncio.create_task(_worker_loop())
