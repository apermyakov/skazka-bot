# -*- coding: utf-8 -*-
"""Fairy tale flow: input → confirm → story → review → photo → generate audio+images → deliver."""

import base64
import logging
import re
from io import BytesIO

from aiogram import Router, types, F, Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import FSInputFile

from bot.states.create import CreateFairyTale
from bot.keyboards.inline import confirm_input, review_story, skip_photo, feedback, main_menu
from engine.pipeline import generate_fairytale
from engine.llm_client import generate_screenplay
from engine.transcribe import transcribe_voice

logger = logging.getLogger(__name__)
router = Router()


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
            lines.append(f"— {clean}")
    return "\n\n".join(lines)


async def _get_text(message: types.Message, bot: Bot) -> tuple[str | None, bool]:
    """Extract text from message. Returns (text, was_voice)."""
    if message.text:
        return message.text.strip(), False

    if message.voice:
        hint = await message.answer("🎤 Распознаю голос...")
        try:
            file = await bot.get_file(message.voice.file_id)
            buf = BytesIO()
            await bot.download_file(file.file_path, buf)
            text = await transcribe_voice(buf.getvalue())
            await hint.edit_text(f"🎤 Распознано: <i>{text}</i>", parse_mode="HTML")
            return text, True
        except Exception as e:
            logger.error("Transcription failed: %s", e, exc_info=True)
            await hint.edit_text("😔 Не удалось распознать. Попробуйте ещё раз или напишите текстом.")
            return None, True

    return None, False


async def _show_story(message: types.Message, state: FSMContext, screenplay: dict):
    """Display the story text with review buttons."""
    title = screenplay["title"]
    story = _clean_story_text(screenplay)

    text = f"📖 <b>{title}</b>\n\n{story}"
    if len(text) > 4000:
        text = text[:4000] + "..."

    await message.answer(text, parse_mode="HTML")
    await message.answer(
        "Нравится сказка? Можно озвучить, внести изменения или сочинить заново.",
        reply_markup=review_story(),
    )
    await state.update_data(screenplay_json=screenplay)
    await state.set_state(CreateFairyTale.reviewing_story)


# ── 1. "Создать сказку" ──
@router.callback_query(F.data == "create")
async def on_create(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
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
    await callback.answer()


# ── 2. Receive input → show confirmation ──
@router.message(CreateFairyTale.waiting_topic, F.text | F.voice)
async def on_input(message: types.Message, state: FSMContext, bot: Bot):
    text, was_voice = await _get_text(message, bot)
    if text is None:
        return

    if len(text) < 10:
        await message.answer("Расскажите чуть подробнее — хотя бы имя ребёнка и тему.")
        return

    await state.update_data(context=text)

    if was_voice:
        label = "🎤 <b>Вот что я услышал:</b>"
    else:
        label = "📝 <b>Ваш запрос:</b>"

    await message.answer(
        f"{label}\n\n<i>{text[:500]}</i>\n\nВсё верно?",
        reply_markup=confirm_input(),
        parse_mode="HTML",
    )
    await state.set_state(CreateFairyTale.confirming_input)


# ── 3. Change input ──
@router.callback_query(F.data == "change_topic")
async def on_change_topic(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("📖 Расскажите заново — текстом или голосовым сообщением:")
    await state.set_state(CreateFairyTale.waiting_topic)
    await callback.answer()


# ── 4. Confirm → compose story ──
@router.callback_query(F.data == "compose_story")
async def on_compose(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    context = data["context"]

    status = await callback.message.answer("📝 Сочиняю сказку...")
    await callback.answer()

    try:
        screenplay = await generate_screenplay(context)
        await status.delete()
        await _show_story(callback.message, state, screenplay)
    except Exception as e:
        logger.error("Screenplay failed: %s", e, exc_info=True)
        await status.edit_text(
            f"😔 Не удалось сочинить сказку: {str(e)[:200]}\nПопробуйте ещё раз!",
            reply_markup=main_menu(),
        )
        await state.clear()


# ── 5. Edit story ──
@router.callback_query(F.data == "edit_story")
async def on_edit(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "✏️ Что изменить? Напишите текстом или голосом, например:\n\n"
        "<i>«Сделай медведя добрее» или «Добавь дракона»</i>",
        parse_mode="HTML",
    )
    await state.set_state(CreateFairyTale.waiting_edits)
    await callback.answer()


@router.message(CreateFairyTale.waiting_edits, F.text | F.voice)
async def on_edits_received(message: types.Message, state: FSMContext, bot: Bot):
    edit_text, _ = await _get_text(message, bot)
    if edit_text is None:
        return

    data = await state.get_data()
    new_context = f"{data.get('context', '')}\n\nИзменения: {edit_text}"
    await state.update_data(context=new_context)

    status = await message.answer("✏️ Переписываю сказку с учётом правок...")
    try:
        screenplay = await generate_screenplay(new_context)
        await status.delete()
        await _show_story(message, state, screenplay)
    except Exception as e:
        logger.error("Edit failed: %s", e, exc_info=True)
        await status.edit_text(f"😔 Ошибка: {str(e)[:200]}", reply_markup=main_menu())
        await state.clear()


# ── 6. Regenerate ──
@router.callback_query(F.data == "regenerate_story")
async def on_regenerate(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    status = await callback.message.answer("🔄 Сочиняю новую версию...")
    await callback.answer()
    try:
        screenplay = await generate_screenplay(data.get("context", ""))
        await status.delete()
        await _show_story(callback.message, state, screenplay)
    except Exception as e:
        logger.error("Regenerate failed: %s", e, exc_info=True)
        await status.edit_text(f"😔 Ошибка: {str(e)[:200]}", reply_markup=main_menu())


# ── 7. "Озвучить" → ask for photo ──
@router.callback_query(F.data == "generate")
async def on_generate_ask_photo(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "🖼 <b>Хотите добавить иллюстрации?</b>\n\n"
        "Отправьте фото ребёнка — и он станет главным героем на картинках к сказке!\n\n"
        "Или нажмите кнопку ниже, чтобы получить сказку без иллюстраций.",
        reply_markup=skip_photo(),
        parse_mode="HTML",
    )
    await state.set_state(CreateFairyTale.waiting_photo)
    await callback.answer()


# ── 8a. Receive photo → start generation ──
@router.message(CreateFairyTale.waiting_photo, F.photo)
async def on_photo_received(message: types.Message, state: FSMContext, bot: Bot):
    # Download the highest resolution photo
    photo = message.photo[-1]  # Last = largest
    file = await bot.get_file(photo.file_id)
    buf = BytesIO()
    await bot.download_file(file.file_path, buf)
    photo_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    await state.update_data(reference_photo_b64=photo_b64)
    await message.answer("📸 Фото получено! Ребёнок будет на иллюстрациях.")
    await _start_generation(message, state)


# ── 8b. Skip photo → start generation without illustrations ──
@router.callback_query(F.data == "skip_photo")
async def on_skip_photo(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(reference_photo_b64=None)
    await callback.answer()
    await _start_generation(callback.message, state)


async def _start_generation(message: types.Message, state: FSMContext):
    """Run the full pipeline: audio + illustrations."""
    await state.set_state(CreateFairyTale.generating)
    data = await state.get_data()
    context = data["context"]
    screenplay = data.get("screenplay_json")
    photo_b64 = data.get("reference_photo_b64")

    status_msg = await message.answer("🎙 Озвучиваю и рисую сказку...")

    async def on_status(msg: str):
        try:
            await status_msg.edit_text(msg, parse_mode="HTML")
        except Exception:
            pass

    audio_sent = False

    async def on_audio_ready(audio_info: dict):
        nonlocal audio_sent
        dur_min = int(audio_info["duration"]) // 60
        dur_sec = int(audio_info["duration"]) % 60

        await status_msg.edit_text(
            f"✅ <b>Аудио готово!</b>\n\n"
            f"📖 <b>{audio_info['title']}</b>\n"
            f"⏱ {dur_min}:{dur_sec:02d}\n\n"
            f"🎨 Рисую иллюстрации...",
            parse_mode="HTML",
        )

        audio_file = FSInputFile(audio_info["file_path"], filename=f"{audio_info['title']}.mp3")
        await message.answer_audio(
            audio=audio_file,
            title=audio_info["title"],
            performer="Сказка на ночь",
            caption="🎧 Включайте — иллюстрации придут в нужный момент!",
        )
        audio_sent = True

    try:
        result = await generate_fairytale(
            context=context,
            screenplay=screenplay,
            reference_photo_b64=photo_b64,
            on_status=on_status,
            on_audio_ready=on_audio_ready,
        )

        # Update status after illustrations are done
        if audio_sent:
            try:
                await status_msg.edit_text(
                    f"✅ <b>Сказка готова!</b>",
                    parse_mode="HTML",
                )
            except Exception:
                pass

        # Fallback: send MP3 if callback didn't fire (shouldn't happen)
        if not audio_sent:
            audio_file = FSInputFile(result["file_path"], filename=f"{result['title']}.mp3")
            await message.answer_audio(
                audio=audio_file,
                title=result["title"],
                performer="Сказка на ночь",
            )

        # Send illustrations timed to scene start
        illustrations = result.get("illustrations", [])
        timecodes = result.get("scene_start_times", [])

        if illustrations and timecodes and len(timecodes) == len(illustrations):
            import asyncio
            for i, (img_path, start_time) in enumerate(zip(illustrations, timecodes)):
                if i == 0:
                    await asyncio.sleep(max(0, start_time))
                else:
                    delay = timecodes[i] - timecodes[i - 1]
                    await asyncio.sleep(max(0, delay))

                await message.answer_photo(
                    photo=FSInputFile(img_path),
                    caption=f"🎨 Сцена {i + 1}",
                )

        # Send MP4 video at the end
        video_path = result.get("video_path")
        if video_path:
            video_file = FSInputFile(video_path, filename=f"{result['title']}.mp4")
            await message.answer_video(
                video=video_file,
                caption=f"🎬 Полная видеосказка «{result['title']}»",
                duration=int(result["duration"]),
            )

        await message.answer("Как вам сказка?", reply_markup=feedback())

    except Exception as e:
        logger.error("Generation failed: %s", e, exc_info=True)
        await status_msg.edit_text(
            f"😔 Не удалось создать сказку: {str(e)[:200]}\nПопробуйте ещё раз!",
            reply_markup=main_menu(),
        )

    await state.clear()


# ── 9. Feedback ──
@router.callback_query(F.data.startswith("fb_"))
async def on_feedback(callback: types.CallbackQuery):
    fb_type = callback.data.replace("fb_", "")
    labels = {"love": "❤️", "ok": "👍", "bad": "👎"}
    label = labels.get(fb_type, "?")
    logger.info("Feedback from user %d: %s", callback.from_user.id, fb_type)
    await callback.message.edit_text(
        f"Спасибо за отзыв {label}!\n\nХотите ещё сказку?",
        reply_markup=main_menu(),
    )
    await callback.answer("Спасибо!")
