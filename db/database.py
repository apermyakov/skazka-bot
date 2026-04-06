# -*- coding: utf-8 -*-
"""Async PostgreSQL database layer for analytics and logging."""

import asyncio
import logging
import os
import traceback as tb_mod
from typing import Any

import asyncpg

from bot.config import settings

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    telegram_id   BIGINT UNIQUE NOT NULL,
    username      TEXT,
    first_name    TEXT,
    last_name     TEXT,
    language_code TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    last_active   TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS stories (
    id                  SERIAL PRIMARY KEY,
    order_id            TEXT UNIQUE,
    user_id             INTEGER REFERENCES users(id),
    title               TEXT,
    context             TEXT,
    was_voice           BOOLEAN DEFAULT FALSE,
    screenplay_json     TEXT,
    ambient             TEXT,
    duration_sec        REAL,
    segments_count      INTEGER,
    illustrations_count INTEGER,
    has_video           BOOLEAN DEFAULT FALSE,
    has_photo           BOOLEAN DEFAULT FALSE,
    photo_count         INTEGER DEFAULT 0,
    feedback            TEXT,
    status              TEXT DEFAULT 'started',
    error_message       TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    completed_at        TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS story_revisions (
    id            SERIAL PRIMARY KEY,
    story_id      INTEGER REFERENCES stories(id),
    revision_type TEXT,
    user_input    TEXT,
    full_context  TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS voice_assignments (
    id              SERIAL PRIMARY KEY,
    story_id        INTEGER REFERENCES stories(id),
    character_id    TEXT,
    character_name  TEXT,
    voice_id        TEXT,
    voice_name      TEXT,
    gender          TEXT,
    age             TEXT,
    role            TEXT,
    score           REAL
);

CREATE TABLE IF NOT EXISTS api_calls (
    id            SERIAL PRIMARY KEY,
    story_id      INTEGER REFERENCES stories(id),
    service       TEXT NOT NULL,
    model         TEXT,
    purpose       TEXT NOT NULL,
    status        TEXT,
    duration_ms   INTEGER,
    request_text  TEXT,
    response_text TEXT,
    input_chars   INTEGER,
    tokens_in     INTEGER,
    tokens_out    INTEGER,
    error         TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS media_files (
    id            SERIAL PRIMARY KEY,
    story_id      INTEGER REFERENCES stories(id),
    file_type     TEXT NOT NULL,
    file_path     TEXT,
    public_url    TEXT,
    file_size     INTEGER,
    width         INTEGER,
    height        INTEGER,
    duration_sec  REAL,
    scene_index   INTEGER,
    mime_type     TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS errors (
    id            SERIAL PRIMARY KEY,
    story_id      INTEGER,
    user_id       INTEGER,
    phase         TEXT,
    error_type    TEXT,
    error_message TEXT,
    traceback     TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);
"""


async def init_db():
    """Create connection pool and initialize schema."""
    global _pool
    try:
        _pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=5)
        async with _pool.acquire() as conn:
            await conn.execute(SCHEMA)
        logger.info("Database initialized: %s", settings.database_url.split("@")[-1])
    except Exception as e:
        logger.error("Database init failed: %s", e)
        _pool = None


async def close_db():
    """Close connection pool."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


async def _safe(coro):
    """Fire-and-forget wrapper — never breaks main flow."""
    try:
        return await coro
    except Exception as e:
        logger.warning("DB write failed: %s", e)
        return None


def fire(coro):
    """Schedule DB write without awaiting. Returns task."""
    return asyncio.create_task(_safe(coro))


# ── Users ──

async def save_user(telegram_id: int, username: str = None, first_name: str = None,
                    last_name: str = None, language_code: str = None) -> int | None:
    if not _pool:
        return None
    async with _pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO users (telegram_id, username, first_name, last_name, language_code)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (telegram_id) DO UPDATE SET
                username = COALESCE($2, users.username),
                first_name = COALESCE($3, users.first_name),
                last_name = COALESCE($4, users.last_name),
                language_code = COALESCE($5, users.language_code),
                last_active = NOW()
            RETURNING id
        """, telegram_id, username, first_name, last_name, language_code)
        return row["id"] if row else None


async def get_user_id(telegram_id: int) -> int | None:
    if not _pool:
        return None
    async with _pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id FROM users WHERE telegram_id = $1", telegram_id)
        return row["id"] if row else None


# ── Stories ──

async def create_story(order_id: str = None, user_id: int = None, context: str = None,
                       was_voice: bool = False) -> int | None:
    if not _pool:
        return None
    async with _pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO stories (order_id, user_id, context, was_voice)
            VALUES ($1, $2, $3, $4)
            RETURNING id
        """, order_id, user_id, context, was_voice)
        return row["id"] if row else None


async def update_story(story_id: int, **fields):
    if not _pool or not story_id or not fields:
        return
    # Build SET clause dynamically
    set_parts = []
    values = []
    for i, (key, val) in enumerate(fields.items(), 1):
        set_parts.append(f"{key} = ${i}")
        values.append(val)
    values.append(story_id)
    query = f"UPDATE stories SET {', '.join(set_parts)} WHERE id = ${len(values)}"
    async with _pool.acquire() as conn:
        await conn.execute(query, *values)


# ── Story revisions ──

async def save_revision(story_id: int, revision_type: str, user_input: str = None,
                        full_context: str = None):
    if not _pool:
        return
    async with _pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO story_revisions (story_id, revision_type, user_input, full_context)
            VALUES ($1, $2, $3, $4)
        """, story_id, revision_type, user_input, full_context)


# ── Voice assignments ──

async def save_voice_assignment(story_id: int, character_id: str, character_name: str,
                                voice_id: str, voice_name: str, gender: str, age: str,
                                role: str, score: float = None):
    if not _pool:
        return
    async with _pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO voice_assignments
                (story_id, character_id, character_name, voice_id, voice_name, gender, age, role, score)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        """, story_id, character_id, character_name, voice_id, voice_name, gender, age, role, score)


# ── API calls ──

async def log_api_call(story_id: int = None, service: str = "", model: str = None,
                       purpose: str = "", status: str = None, duration_ms: int = None,
                       request_text: str = None, response_text: str = None,
                       input_chars: int = None, tokens_in: int = None,
                       tokens_out: int = None, error: str = None):
    if not _pool:
        return
    async with _pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO api_calls
                (story_id, service, model, purpose, status, duration_ms,
                 request_text, response_text, input_chars, tokens_in, tokens_out, error)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
        """, story_id, service, model, purpose, status, duration_ms,
             request_text, response_text, input_chars, tokens_in, tokens_out, error)


# ── Media files ──

async def save_media_file(story_id: int, file_type: str, file_path: str,
                          file_size: int = None, width: int = None, height: int = None,
                          duration_sec: float = None, scene_index: int = None,
                          mime_type: str = None) -> str | None:
    """Save media file record and return public URL."""
    if not _pool:
        return None
    # Build public URL from file_path (e.g. media/abc123/final.mp3 → http://host/media/abc123/final.mp3)
    relative = file_path
    if relative.startswith("/app/"):
        relative = relative[5:]
    public_url = f"{settings.media_base_url}/{relative.lstrip('media/')}"

    async with _pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO media_files
                (story_id, file_type, file_path, public_url, file_size, width, height,
                 duration_sec, scene_index, mime_type)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        """, story_id, file_type, file_path, public_url, file_size, width, height,
             duration_sec, scene_index, mime_type)
    return public_url


# ── Errors ──

async def log_error(story_id: int = None, user_id: int = None, phase: str = None,
                    error_type: str = None, error_message: str = None,
                    traceback_str: str = None):
    if not _pool:
        return
    async with _pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO errors (story_id, user_id, phase, error_type, error_message, traceback)
            VALUES ($1, $2, $3, $4, $5, $6)
        """, story_id, user_id, phase, error_type, error_message, traceback_str)


# ── Feedback ──

async def save_feedback(story_id: int, feedback: str):
    if not _pool or not story_id:
        return
    await update_story(story_id, feedback=feedback)
