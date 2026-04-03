# -*- coding: utf-8 -*-
"""Start handler — welcome message."""

from aiogram import Router, types
from aiogram.filters import CommandStart

from bot.keyboards.inline import main_menu

router = Router()

WELCOME_TEXT = (
    "🌙 <b>Сказка на ночь</b>\n\n"
    "Я создаю уникальные аудиосказки для вашего ребёнка. "
    "Несколько голосов, звуки природы и волшебная история — "
    "всё за пару минут.\n\n"
    "Просто назовите тему — и я сочиню и озвучу сказку!"
)


@router.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer(WELCOME_TEXT, reply_markup=main_menu(), parse_mode="HTML")
