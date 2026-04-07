# -*- coding: utf-8 -*-
"""Story composition and editing: confirm -> compose, edit, regenerate."""

import logging
import traceback as tb_mod

from aiogram import Router, types, F, Bot
from aiogram.fsm.context import FSMContext

from bot.states.create import CreateFairyTale
from bot.keyboards.inline import main_menu
from engine.llm_client import generate_story_text
from db.database import (
    get_user_id, create_story, update_story,
    save_revision, log_error, check_rate_limit, fire,
)

from bot.handlers.utils import (
    _msg, _guard, _dismiss, _get_text, _show_story, _ensure_user,
)

logger = logging.getLogger(__name__)
router = Router()


async def _generate_and_show(message: types.Message, state: FSMContext,
                              context: str, story_id: int | None,
                              status_text: str = "📝 Сочиняю сказку...",
                              sticker: bool = True):
    """Common helper: show status, call LLM, display result."""
    from db.config_manager import cfg
    if sticker:
        composing_sticker = await cfg.get("ui.sticker_composing", None)
        if composing_sticker:
            status = await message.answer_sticker(composing_sticker)
        else:
            status = await message.answer(status_text)
    else:
        status = await message.answer(status_text)

    try:
        story_result = await generate_story_text(context, story_id=story_id)
        if story_id:
            fire(update_story(story_id, title=story_result["title"]))
        await status.delete()
        await state.update_data(_busy=False)
        await _show_story(message, state, story_result["title"], story_result["text"])
    except Exception as e:
        await state.update_data(_busy=False)
        logger.error("Story generation failed: %s", e, exc_info=True)
        if story_id:
            fire(log_error(story_id=story_id, phase="compose",
                           error_type=type(e).__name__, error_message=str(e),
                           traceback_str=tb_mod.format_exc()))
        try:
            await status.delete()
        except Exception:
            pass
        await message.answer(f"😔 Ошибка: {str(e)[:200]}", reply_markup=main_menu())
        await state.clear()


# -- 4. Confirm -> compose story --
@router.callback_query(F.data == "compose_story")
async def on_compose(callback: types.CallbackQuery, state: FSMContext):
    import time as _time
    t0 = _time.time()
    if await _guard(state, message=callback.message):
        await callback.answer()
        return
    data = await state.get_data()
    context = data["context"]
    was_voice = data.get("was_voice", False)

    logger.info("[TIMING] guard+state: %.1fms", (_time.time() - t0) * 1000)

    # Rate limit + create story in DB
    t1 = _time.time()
    db_user_id = await get_user_id(callback.from_user.id)
    if db_user_id and not await check_rate_limit(db_user_id):
        await state.update_data(_busy=False)
        await callback.message.answer(await _msg("msg.rate_limit", "⚠️ Вы создали слишком много сказок за последний час. Попробуйте позже."))
        return
    story_id = await create_story(user_id=db_user_id, context=context, was_voice=was_voice)
    await state.update_data(db_story_id=story_id)
    logger.info("[TIMING] DB write: %.1fms", (_time.time() - t1) * 1000)

    t2 = _time.time()
    from db.config_manager import cfg
    composing_sticker = await cfg.get("ui.sticker_composing", None)
    if composing_sticker:
        status = await callback.message.answer_sticker(composing_sticker)
    else:
        status = await callback.message.answer("📝 Сочиняю сказку...")
    await _dismiss(callback)
    logger.info("[TIMING] Telegram answer+dismiss: %.1fms", (_time.time() - t2) * 1000)

    try:
        t3 = _time.time()
        story_result = await generate_story_text(context, story_id=story_id)
        logger.info("[TIMING] LLM screenplay: %.1fms", (_time.time() - t3) * 1000)
        if story_id:
            fire(update_story(story_id, title=story_result["title"], status="screenplay"))
        await status.delete()
        await state.update_data(_busy=False)
        await _show_story(callback.message, state, story_result["title"], story_result["text"])
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
        await callback.message.answer(
            f"😔 Не удалось сочинить сказку: {str(e)[:200]}\nПопробуйте ещё раз!",
            reply_markup=main_menu(),
        )
        await state.clear()


# -- 5. Edit story (button or direct message) --
@router.callback_query(F.data == "edit_story")
async def on_edit(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "✏️ Напишите что изменить:",
        parse_mode="HTML",
    )
    await state.set_state(CreateFairyTale.waiting_edits)
    await _dismiss(callback)


# Direct text/voice while reviewing -> treat as edit
@router.message(CreateFairyTale.reviewing_story, F.text | F.voice)
async def on_direct_edit(message: types.Message, state: FSMContext, bot: Bot):
    """User sends text/voice while reviewing story -- treat as edit request."""
    if await _guard(state, message=message):
        return
    edit_text, was_voice = await _get_text(message, bot)
    if edit_text is None:
        await state.update_data(_busy=False)
        return

    # Show what was recognized from voice
    if was_voice:
        await message.answer(f"✏️ <i>{edit_text}</i>", parse_mode="HTML")

    data = await state.get_data()
    new_context = f"{data.get('context', '')}\n\nИзменения: {edit_text}"
    await state.update_data(context=new_context)

    story_id = data.get("db_story_id")
    if story_id:
        fire(save_revision(story_id, revision_type="edit", user_input=edit_text, full_context=new_context))

    from db.config_manager import cfg
    composing_sticker = await cfg.get("ui.sticker_composing", None)
    if composing_sticker:
        status = await message.answer_sticker(composing_sticker)
    else:
        status = await message.answer("✏️ Переписываю сказку...")
    try:
        story_result = await generate_story_text(new_context, story_id=story_id)
        if story_id:
            fire(update_story(story_id, title=story_result["title"]))
        await status.delete()
        await state.update_data(_busy=False)
        await _show_story(message, state, story_result["title"], story_result["text"])
    except Exception as e:
        await state.update_data(_busy=False)
        logger.error("Direct edit failed: %s", e, exc_info=True)
        if story_id:
            fire(log_error(story_id=story_id, phase="edit",
                           error_type=type(e).__name__, error_message=str(e),
                           traceback_str=tb_mod.format_exc()))
        try:
            await status.delete()
        except Exception:
            pass
        await message.answer(f"😔 Ошибка: {str(e)[:200]}", reply_markup=main_menu())
        await state.clear()


@router.message(CreateFairyTale.waiting_edits, F.text | F.voice)
async def on_edits_received(message: types.Message, state: FSMContext, bot: Bot):
    edit_text, _ = await _get_text(message, bot)
    if edit_text is None:
        return

    data = await state.get_data()
    new_context = f"{data.get('context', '')}\n\nИзменения: {edit_text}"
    await state.update_data(context=new_context)

    story_id = data.get("db_story_id")
    if story_id:
        fire(save_revision(story_id, revision_type="edit", user_input=edit_text, full_context=new_context))

    status = await message.answer("✏️ Переписываю сказку с учётом правок...")
    try:
        story_result = await generate_story_text(new_context, story_id=story_id)
        if story_id:
            fire(update_story(story_id, title=story_result["title"]))
        await status.delete()
        await _show_story(message, state, story_result["title"], story_result["text"])
    except Exception as e:
        logger.error("Edit failed: %s", e, exc_info=True)
        if story_id:
            fire(log_error(story_id=story_id, phase="edit",
                           error_type=type(e).__name__, error_message=str(e),
                           traceback_str=tb_mod.format_exc()))
        await status.edit_text(f"😔 Ошибка: {str(e)[:200]}", reply_markup=main_menu())
        await state.clear()


# -- 6. Regenerate --
@router.callback_query(F.data == "regenerate_story")
async def on_regenerate(callback: types.CallbackQuery, state: FSMContext):
    if await _guard(state, message=callback.message):
        await callback.answer()
        return
    data = await state.get_data()
    story_id = data.get("db_story_id")
    if story_id:
        fire(save_revision(story_id, revision_type="regenerate", full_context=data.get("context", "")))

    status = await callback.message.answer("🔄 Сочиняю новую версию...")
    await _dismiss(callback)
    try:
        story_result = await generate_story_text(data.get("context", ""), story_id=story_id)
        if story_id:
            fire(update_story(story_id, title=story_result["title"]))
        await status.delete()
        await state.update_data(_busy=False)
        await _show_story(callback.message, state, story_result["title"], story_result["text"])
    except Exception as e:
        await state.update_data(_busy=False)
        logger.error("Regenerate failed: %s", e, exc_info=True)
        if story_id:
            fire(log_error(story_id=story_id, phase="regenerate",
                           error_type=type(e).__name__, error_message=str(e),
                           traceback_str=tb_mod.format_exc()))
        await status.edit_text(f"😔 Ошибка: {str(e)[:200]}", reply_markup=main_menu())
