# -*- coding: utf-8 -*-
"""Inline keyboards for the bot."""

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✨ Создать сказку", callback_data="create")],
    ])


def confirm_input() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Сочинить сказку", callback_data="compose_story")],
    ])


def review_story() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🐢 Медленно", callback_data="generate:slow"),
            InlineKeyboardButton(text="🚶 Нормально", callback_data="generate:normal"),
            InlineKeyboardButton(text="🐇 Быстро", callback_data="generate:fast"),
        ],
    ])


def skip_photo() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Без иллюстраций", callback_data="skip_photo")],
    ])


def photos_done() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎙 Озвучить без фото", callback_data="skip_photo")],
    ])


def feedback() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="❤️ Супер!", callback_data="fb_love"),
            InlineKeyboardButton(text="👍 Нормально", callback_data="fb_ok"),
            InlineKeyboardButton(text="👎 Не очень", callback_data="fb_bad"),
        ],
        [InlineKeyboardButton(text="✨ Создать ещё", callback_data="create")],
    ])
