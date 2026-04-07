# -*- coding: utf-8 -*-
"""Real-time error notifications to admin via Telegram."""

import logging
import traceback as tb_mod
from typing import Any

from bot.config import settings

logger = logging.getLogger(__name__)

_bot = None


def set_bot(bot):
    """Set the bot instance for sending notifications."""
    global _bot
    _bot = bot


async def notify_admin(text: str):
    """Send a message to all admins. Truncates to Telegram limit."""
    if not _bot:
        return
    msg = text[:4000]
    for admin_id in settings.admin_id_list:
        try:
            await _bot.send_message(admin_id, msg, parse_mode="HTML")
        except Exception as e:
            logger.warning("Failed to notify admin %d: %s", admin_id, e)


async def notify_error(
    error: Exception,
    user_id: int = None,
    username: str = None,
    phase: str = None,
    story_title: str = None,
    context: str = None,
):
    """Send formatted error notification to admin."""
    tb = tb_mod.format_exception(type(error), error, error.__traceback__)
    tb_text = "".join(tb)[-1500:]  # last 1500 chars of traceback

    parts = [f"🚨 <b>Ошибка: {phase or 'unknown'}</b>\n"]
    if username:
        parts.append(f"👤 @{username} (id: {user_id})")
    elif user_id:
        parts.append(f"👤 id: {user_id}")
    if story_title:
        parts.append(f"📖 {story_title}")
    if context:
        parts.append(f"📝 <i>{context[:200]}</i>")
    parts.append(f"\n<pre>{tb_text}</pre>")

    await notify_admin("\n".join(parts))


async def notify_new_user(user_id: int, username: str = None, first_name: str = None):
    """Notify admin about a new user."""
    name = f"@{username}" if username else first_name or str(user_id)
    await notify_admin(f"👋 Новый пользователь: {name} (id: {user_id})")


async def notify_story_complete(
    user_id: int,
    username: str = None,
    title: str = None,
    duration: float = None,
    video_url: str = None,
    audio_url: str = None,
):
    """Notify admin about completed story with media links."""
    name = f"@{username}" if username else str(user_id)
    dur = f"{int(duration)//60}:{int(duration)%60:02d}" if duration else "?"
    parts = [f"✅ «{title}» ({dur}) — {name}"]
    if video_url:
        parts.append(f"🎬 <a href=\"{video_url}\">Видео</a>")
    if audio_url:
        parts.append(f"🎧 <a href=\"{audio_url}\">Аудио</a>")
    await notify_admin("\n".join(parts))
