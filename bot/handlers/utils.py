# -*- coding: utf-8 -*-
"""Shared helpers for fairy-tale handlers."""

import html as _html
import logging
import re
from io import BytesIO

from aiogram import types, Bot
from aiogram.fsm.context import FSMContext

from bot.config import settings
from bot.states.create import CreateFairyTale
from engine.transcribe import transcribe_voice
from db.database import save_user

logger = logging.getLogger(__name__)

# ── Constants ──
MAX_TEXT_LENGTH = 2000
MAX_VOICE_DURATION = 60  # seconds
MAX_PHOTO_SIZE = 10 * 1024 * 1024  # 10MB


async def _msg(key: str, default: str, **kwargs) -> str:
    """Get system message from config, with format args."""
    from db.config_manager import cfg
    template = await cfg.get(key, default)
    try:
        return template.format(**kwargs) if kwargs else template
    except (KeyError, IndexError):
        return default


async def _guard(state: FSMContext, message=None, key: str = "_busy") -> bool:
    """Prevent double-clicks. Returns True if already busy (should skip)."""
    data = await state.get_data()
    if data.get(key):
        if message:
            try:
                text = await _msg("msg.busy", "\u23f3 \u0412\u0430\u0448 \u0437\u0430\u043f\u0440\u043e\u0441 \u043e\u0431\u0440\u0430\u0431\u0430\u0442\u044b\u0432\u0430\u0435\u0442\u0441\u044f. \u041f\u043e\u0436\u0430\u043b\u0443\u0439\u0441\u0442\u0430, \u043f\u043e\u0434\u043e\u0436\u0434\u0438\u0442\u0435.")
                await message.answer(text)
            except Exception:
                pass
        return True
    await state.update_data(**{key: True})
    return False


async def _dismiss(callback: types.CallbackQuery):
    """Remove inline buttons from the message after user clicks one."""
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.answer()


async def _get_text(message: types.Message, bot: Bot) -> tuple[str | None, bool]:
    """Extract text from message. Returns (text, was_voice)."""
    if message.text:
        text = message.text.strip()
        if len(text) > MAX_TEXT_LENGTH:
            text = text[:MAX_TEXT_LENGTH]
            await message.answer(f"\u26a0\ufe0f \u0422\u0435\u043a\u0441\u0442 \u043e\u0431\u0440\u0435\u0437\u0430\u043d \u0434\u043e {MAX_TEXT_LENGTH} \u0441\u0438\u043c\u0432\u043e\u043b\u043e\u0432.")
        return text, False

    if message.voice:
        if message.voice.duration and message.voice.duration > MAX_VOICE_DURATION:
            await message.answer(f"\u26a0\ufe0f \u0413\u043e\u043b\u043e\u0441\u043e\u0432\u043e\u0435 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435 \u0441\u043b\u0438\u0448\u043a\u043e\u043c \u0434\u043b\u0438\u043d\u043d\u043e\u0435 (\u043c\u0430\u043a\u0441 {MAX_VOICE_DURATION} \u0441\u0435\u043a).")
            return None, True
        hint = await message.answer(await _msg("msg.voice_recognizing", "\ud83c\udf99 \u0420\u0430\u0441\u043f\u043e\u0437\u043d\u0430\u044e \u0433\u043e\u043b\u043e\u0441..."))
        try:
            file = await bot.get_file(message.voice.file_id)
            buf = BytesIO()
            await bot.download_file(file.file_path, buf)
            text = await transcribe_voice(buf.getvalue())
            await hint.delete()
            return text, True
        except Exception as e:
            logger.error("Transcription failed: %s", e, exc_info=True)
            await hint.edit_text(await _msg("msg.voice_failed", "\ud83d\ude14 \u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0440\u0430\u0441\u043f\u043e\u0437\u043d\u0430\u0442\u044c. \u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439\u0442\u0435 \u0435\u0449\u0451 \u0440\u0430\u0437 \u0438\u043b\u0438 \u043d\u0430\u043f\u0438\u0448\u0438\u0442\u0435 \u0442\u0435\u043a\u0441\u0442\u043e\u043c."))
            return None, True

    return None, False


def _clean_story_text(screenplay: dict) -> str:
    """Build clean readable text from screenplay, stripping audio tags."""
    lines = []
    for seg in screenplay["segments"]:
        raw = seg["text"]
        clean = re.sub(r'\[[\w\s]+\]', '', raw).strip()
        clean = re.sub(r'\s{2,}', ' ', clean)
        if not clean:
            continue
        if seg["character_id"] == "narrator":
            lines.append(clean)
        else:
            lines.append(f"\u2014 {clean}")
    return "\n\n".join(lines)


def _clean_for_display(story_text: str) -> str:
    """Remove character name prefixes (e.g. '\u0420\u0430\u0441\u0441\u043a\u0430\u0437\u0447\u0438\u043a: ') for user display."""
    lines = []
    for line in story_text.split("\n"):
        stripped = line.strip()
        if ":" in stripped:
            # Check if it's a character label (short prefix before colon)
            prefix, _, rest = stripped.partition(":")
            if len(prefix) < 30 and rest.strip():
                # It's "Name: dialogue" -- show as dialogue
                lines.append(f"\u2014 {rest.strip()}" if prefix.strip().lower() != "\u0440\u0430\u0441\u0441\u043a\u0430\u0437\u0447\u0438\u043a" else rest.strip())
            else:
                lines.append(stripped)
        else:
            lines.append(stripped)
    return "\n".join(lines)


def _sanitize_text(text: str) -> str:
    """Remove surrogate characters that break UTF-8 encoding."""
    return text.encode("utf-8", errors="replace").decode("utf-8")


async def _show_story(message: types.Message, state: FSMContext, title: str, story_text: str):
    """Display the story text split into Telegram-safe chunks with buttons on last."""
    display_text = _html.escape(_clean_for_display(_sanitize_text(story_text)))
    safe_title = _html.escape(_sanitize_text(title))
    full_text = f"\U0001f4d6 <b>{safe_title}</b>\n\n{display_text}"

    # Split into chunks of ~3900 chars at paragraph boundaries
    chunks = []
    current = ""
    for para in full_text.split("\n\n"):
        if len(current) + len(para) + 2 > 3900 and current:
            chunks.append(current.strip())
            current = para
        else:
            current = current + "\n\n" + para if current else para
    if current.strip():
        chunks.append(current.strip())

    if not chunks:
        chunks = [full_text[:3900]]

    # Send all chunks, buttons on the last one
    from bot.keyboards.inline import review_story
    for i, chunk in enumerate(chunks):
        if i == len(chunks) - 1:
            await message.answer(chunk, reply_markup=review_story(), parse_mode="HTML")
        else:
            await message.answer(chunk, parse_mode="HTML")

    await state.update_data(story_title=title, story_text=story_text)
    await state.set_state(CreateFairyTale.reviewing_story)


async def _ensure_user(user: types.User) -> int | None:
    """Save/update user in DB and return internal user_id."""
    return await save_user(
        telegram_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        language_code=user.language_code,
    )
