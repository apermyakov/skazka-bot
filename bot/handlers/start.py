# -*- coding: utf-8 -*-
"""Start handler — welcome message."""

from aiogram import Router, types
from aiogram.filters import CommandStart

from bot.keyboards.inline import main_menu
from db.database import save_user, fire

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
    fire(save_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        language_code=message.from_user.language_code,
    ))
    await message.answer(WELCOME_TEXT, reply_markup=main_menu(), parse_mode="HTML")
