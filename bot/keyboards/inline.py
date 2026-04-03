# -*- coding: utf-8 -*-
"""Inline keyboards for the bot."""

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✨ Создать сказку", callback_data="create")],
    ])


def review_story() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎙 Озвучить сказку", callback_data="generate")],
        [InlineKeyboardButton(text="✏️ Внести изменения", callback_data="edit_story")],
        [InlineKeyboardButton(text="🔄 Сочинить заново", callback_data="regenerate_story")],
    ])


def confirm_generate(topic: str) -> InlineKeyboardMarkup:
    """Legacy — redirect to review_story."""
    return review_story()


def feedback() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="❤️ Супер!", callback_data="fb_love"),
            InlineKeyboardButton(text="👍 Нормально", callback_data="fb_ok"),
            InlineKeyboardButton(text="👎 Не очень", callback_data="fb_bad"),
        ],
        [InlineKeyboardButton(text="✨ Создать ещё", callback_data="create")],
    ])
