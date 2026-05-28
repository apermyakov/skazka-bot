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


STYLE_LABELS = {
    "painted": "🎨 Живопись",
    "watercolor": "🖌 Акварель",
    "realistic": "🎬 Реализм",
    "pixar": "✨ Pixar",
}


def review_story(selected_style: str = "painted") -> InlineKeyboardMarkup:
    """Story review keyboard: a style-picker (toggle) + a speed row that triggers generation.

    Tapping a style updates the selection (marked with ✅); tapping a speed starts generation.
    """
    def style_btn(key: str) -> InlineKeyboardButton:
        label = STYLE_LABELS[key]
        if key == selected_style:
            label = f"✅ {label}"
        return InlineKeyboardButton(text=label, callback_data=f"style:{key}")

    return InlineKeyboardMarkup(inline_keyboard=[
        [style_btn("painted"), style_btn("watercolor")],
        [style_btn("realistic"), style_btn("pixar")],
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
