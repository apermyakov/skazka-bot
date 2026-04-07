# -*- coding: utf-8 -*-
"""Start handler — welcome message + admin commands."""

from aiogram import Router, types
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext

from bot.keyboards.inline import main_menu
from bot.notify import notify_new_user
from bot.states.create import CreateFairyTale
from db.database import save_user, get_user_id, fire

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
async def cmd_start(message: types.Message, state: FSMContext):
    # Block /start during generation
    current_state = await state.get_state()
    if current_state == CreateFairyTale.generating:
        from db.config_manager import cfg
        text = await cfg.get("msg.start_during_generation", "⏳ Сказка ещё создаётся. Пожалуйста, подождите.")
        await message.answer(text)
        return

    # Check if new user
    existing = await get_user_id(message.from_user.id)

    fire(save_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        language_code=message.from_user.language_code,
    ))

    if not existing:
        fire(notify_new_user(
            user_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
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
