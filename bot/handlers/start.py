# -*- coding: utf-8 -*-
"""Start handler — welcome message + admin commands."""

from aiogram import Router, types
from aiogram.filters import CommandStart, Command

from bot.keyboards.inline import main_menu
from db.database import save_user, fire

router = Router()

ADMIN_ID = 119993853

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


@router.message(Command("reload"))
async def cmd_reload(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    from db.config_manager import cfg
    await cfg._reload()
    count = len(cfg._cache)
    await message.answer(f"Config reloaded: {count} keys")
