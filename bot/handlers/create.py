# -*- coding: utf-8 -*-
"""Entry points and input handlers: /new, create callback, topic input, confirmation."""

import logging
import traceback as tb_mod

from aiogram import Router, types, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from bot.states.create import CreateFairyTale
from bot.keyboards.inline import confirm_input, main_menu
from engine.llm_client import generate_story_text
from db.database import get_user_id, create_story, update_story, log_error, fire

from bot.handlers.utils import (
    _msg, _guard, _dismiss, _get_text, _show_story, _ensure_user,
)

logger = logging.getLogger(__name__)
router = Router()


# -- 1. "Создать сказку" (кнопка или /new) --
@router.message(Command("new"))
async def cmd_new(message: types.Message, state: FSMContext):
    """Slash command to start a new story."""
    await state.clear()
    fire(_ensure_user(message.from_user))
    await message.answer(
        "📖 <b>Расскажите мне всё для сказки!</b>\n\n"
        "Отправьте <b>одно сообщение</b> (текстом или голосом):\n\n"
        "🧒 <b>Кто ваш ребёнок?</b> Имя, возраст\n"
        "❤️ <b>Что любит?</b> Увлечения, любимые игрушки\n"
        "😨 <b>Чего боится?</b> (если хотите, чтобы сказка помогла)\n"
        "💬 <b>Любимые фразы?</b>\n"
        "📖 <b>Тема или сюжет?</b> (необязательно)\n\n"
        "<i>Например: «Мой сын Даня, 5 лет, обожает динозавров и космос. "
        "Боится темноты. Сделай сказку про динозавра в космосе.»</i>",
        parse_mode="HTML",
    )
    await state.set_state(CreateFairyTale.waiting_topic)


@router.callback_query(F.data == "create")
async def on_create(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    fire(_ensure_user(callback.from_user))
    await callback.message.answer(
        "📖 <b>Расскажите мне всё для сказки!</b>\n\n"
        "Отправьте <b>одно сообщение</b> (текстом или голосом):\n\n"
        "🧒 <b>Кто ваш ребёнок?</b> Имя, возраст\n"
        "❤️ <b>Что любит?</b> Увлечения, любимые игрушки\n"
        "😨 <b>Чего боится?</b> (если хотите, чтобы сказка помогла)\n"
        "💬 <b>Любимые фразы?</b>\n"
        "📖 <b>Тема или сюжет?</b> (необязательно)\n\n"
        "<i>Например: «Мой сын Даня, 5 лет, обожает динозавров и космос. "
        "Боится темноты. Сделай сказку про динозавра в космосе.»</i>",
        parse_mode="HTML",
    )
    await state.set_state(CreateFairyTale.waiting_topic)
    await _dismiss(callback)


# -- 2. Receive input -> show confirmation --
@router.message(CreateFairyTale.waiting_topic, F.text | F.voice)
async def on_input(message: types.Message, state: FSMContext, bot: Bot):
    text, was_voice = await _get_text(message, bot)
    if text is None:
        return

    if len(text) < 10:
        await message.answer(await _msg("msg.text_too_short", "Расскажите чуть подробнее — хотя бы имя ребёнка и тему."))
        return

    await state.update_data(context=text, was_voice=was_voice)

    if was_voice:
        # Voice input -> show transcription and ask to confirm
        await message.answer(
            f"🎤 <b>Вот что я услышал:</b>\n\n<i>{text[:500]}</i>\n\nВсё верно?",
            reply_markup=confirm_input(),
            parse_mode="HTML",
        )
        await state.set_state(CreateFairyTale.confirming_input)
    else:
        # Text input -> go straight to screenplay generation
        fire(_ensure_user(message.from_user))
        db_user_id = await get_user_id(message.from_user.id)
        story_id = await create_story(user_id=db_user_id, context=text, was_voice=False)
        await state.update_data(db_story_id=story_id)

        from db.config_manager import cfg
        composing_sticker = await cfg.get("ui.sticker_composing", None)
        if composing_sticker:
            status = await message.answer_sticker(composing_sticker)
        else:
            status = await message.answer("📝 Сочиняю сказку...")
        try:
            story_result = await generate_story_text(text, story_id=story_id)
            if story_id:
                fire(update_story(story_id, title=story_result["title"], status="screenplay"))
            await status.delete()
            await state.update_data(_busy=False)
            await _show_story(message, state, story_result["title"], story_result["text"])
        except Exception as e:
            await state.update_data(_busy=False)
            logger.error("Screenplay failed: %s", e, exc_info=True)
            if story_id:
                fire(update_story(story_id, status="failed", error_message=str(e)[:500]))
                fire(log_error(story_id=story_id, user_id=db_user_id, phase="screenplay",
                               error_type=type(e).__name__, error_message=str(e),
                               traceback_str=tb_mod.format_exc()))
            try:
                await status.delete()
            except Exception:
                pass
            await message.answer(
                f"😔 Не удалось сочинить сказку: {str(e)[:200]}\nПопробуйте ещё раз!",
                reply_markup=main_menu(),
            )
            await state.clear()


# -- 3. Change input (button or new message while confirming) --
@router.callback_query(F.data == "change_topic")
async def on_change_topic(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer(await _msg("msg.retry_topic", "📖 Расскажите заново:"))
    await state.set_state(CreateFairyTale.waiting_topic)
    await _dismiss(callback)


@router.message(CreateFairyTale.confirming_input, F.text | F.voice)
async def on_replace_input(message: types.Message, state: FSMContext, bot: Bot):
    """User sends new text/voice while confirming -- replaces previous input."""
    if await _guard(state, message=message):
        return
    text, was_voice = await _get_text(message, bot)
    if text is None:
        await state.update_data(_busy=False)
        return
    if len(text) < 10:
        await state.update_data(_busy=False)
        await message.answer(await _msg("msg.text_too_short", "Расскажите чуть подробнее — хотя бы имя ребёнка и тему."))
        return
    await state.update_data(context=text, was_voice=was_voice, _busy=False)
    if was_voice:
        await message.answer(
            f"🎤 <b>Вот что я услышал:</b>\n\n<i>{text[:500]}</i>\n\nВсё верно?",
            reply_markup=confirm_input(),
            parse_mode="HTML",
        )
    else:
        # Text -> go straight to generation
        fire(_ensure_user(message.from_user))
        db_user_id = await get_user_id(message.from_user.id)
        story_id = await create_story(user_id=db_user_id, context=text, was_voice=False)
        await state.update_data(db_story_id=story_id)
        from db.config_manager import cfg
        composing_sticker = await cfg.get("ui.sticker_composing", None)
        if composing_sticker:
            status = await message.answer_sticker(composing_sticker)
        else:
            status = await message.answer("📝 Сочиняю сказку...")
        try:
            story_result = await generate_story_text(text, story_id=story_id)
            if story_id:
                fire(update_story(story_id, title=story_result["title"], status="screenplay"))
            await status.delete()
            await _show_story(message, state, story_result["title"], story_result["text"])
        except Exception as e:
            logger.error("Screenplay failed: %s", e, exc_info=True)
            try:
                await status.delete()
            except Exception:
                pass
            await message.answer(f"😔 Ошибка: {str(e)[:200]}", reply_markup=main_menu())
            await state.clear()
