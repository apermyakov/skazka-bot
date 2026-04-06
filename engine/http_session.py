# -*- coding: utf-8 -*-
"""Shared aiohttp session for all engine HTTP calls.

Reuses TCP connections, TLS sessions, and connection pools
instead of creating a new session per request.
"""

import aiohttp

_session: aiohttp.ClientSession | None = None


def get_session() -> aiohttp.ClientSession:
    """Get or create the shared aiohttp session."""
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=120),
        )
    return _session


async def close_session():
    """Close the shared session (call on shutdown)."""
    global _session
    if _session and not _session.closed:
        await _session.close()
        _session = None
