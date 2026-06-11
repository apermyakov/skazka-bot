"""Create the inbox_messages table for incoming-email tracking.

Idempotent — safe to re-run. Adds the table + the unique index on
message_id used by the IMAP poller to deduplicate when polls overlap.
"""
import asyncio, sys
sys.path.insert(0, "/app")

SCHEMA = """
CREATE TABLE IF NOT EXISTS inbox_messages (
    id           BIGSERIAL PRIMARY KEY,
    message_id   TEXT NOT NULL,                       -- RFC822 Message-ID header
    in_reply_to  TEXT,                                -- threading
    "references" TEXT,                                -- threading

    from_addr    TEXT NOT NULL,
    from_name    TEXT,
    to_addr      TEXT,
    cc_addr      TEXT,
    subject      TEXT,
    body_text    TEXT,
    body_html    TEXT,

    received_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- correlations populated by the matcher
    order_id     TEXT,
    feedback_id  INT,
    thread_key   TEXT,                                -- normalised subject for thread grouping

    -- workflow
    status       TEXT NOT NULL DEFAULT 'new',         -- new | replied | archived | spam
    reply_id     INT,                                 -- FK to email_outbox.id
    replied_at   TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS inbox_messages_msgid_uq ON inbox_messages (message_id);
CREATE INDEX IF NOT EXISTS inbox_messages_thread_idx     ON inbox_messages (thread_key, received_at);
CREATE INDEX IF NOT EXISTS inbox_messages_status_idx     ON inbox_messages (status, received_at DESC);
CREATE INDEX IF NOT EXISTS inbox_messages_from_idx       ON inbox_messages (from_addr, received_at DESC);
"""


async def main():
    import db.database as dbmod
    if dbmod._pool is None:
        await dbmod.init_db()
    async with dbmod._pool.acquire() as c:
        await c.execute(SCHEMA)
    print("  inbox_messages: ready")


if __name__ == "__main__":
    asyncio.run(main())
