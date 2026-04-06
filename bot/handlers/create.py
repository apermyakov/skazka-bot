# -*- coding: utf-8 -*-
"""Fairy tale flow: input → confirm → story → review → photo → generate audio+images → deliver."""

import base64
import json
import logging
import os
import re
import traceback as tb_mod
from io import BytesIO

from aiogram import Router, types, F, Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import FSInputFile

from bot.states.create import CreateFairyTale
from bot.keyboards.inline import confirm_input, review_story, skip_photo, photos_done, feedback, main_menu
from engine.pipeline import generate_fairytale
from engine.llm_client import generate_screenplay
from engine.transcribe import transcribe_voice
from db.database import (
    save_user, get_user_id, create_story, update_story,
    save_revision, log_api_call, log_error, save_feedback,
    save_media_file, fire,
)

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


async def _ensure_user(user: types.User) -> int | None:
    """Save/update user in DB and return internal user_id."""
    return await save_user(
        telegram_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        language_code=user.language_code,
    )


# ── 1. "Создать сказку" ──
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

    await state.update_data(context=text, was_voice=was_voice)

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
    was_voice = data.get("was_voice", False)

    # Create story in DB
    db_user_id = await get_user_id(callback.from_user.id)
    story_id = await create_story(user_id=db_user_id, context=context, was_voice=was_voice)
    await state.update_data(db_story_id=story_id)

    status = await callback.message.answer("📝 Сочиняю сказку...")
    await callback.answer()

    try:
        screenplay = await generate_screenplay(context, story_id=story_id)
        if story_id:
            fire(update_story(story_id, title=screenplay.get("title"),
                              screenplay_json=json.dumps(screenplay, ensure_ascii=False),
                              status="screenplay"))
        await status.delete()
        await _show_story(callback.message, state, screenplay)
    except Exception as e:
        logger.error("Screenplay failed: %s", e, exc_info=True)
        if story_id:
            fire(update_story(story_id, status="failed", error_message=str(e)[:500]))
            fire(log_error(story_id=story_id, user_id=db_user_id, phase="screenplay",
                           error_type=type(e).__name__, error_message=str(e),
                           traceback_str=tb_mod.format_exc()))
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

    story_id = data.get("db_story_id")
    if story_id:
        fire(save_revision(story_id, revision_type="edit", user_input=edit_text, full_context=new_context))

    status = await message.answer("✏️ Переписываю сказку с учётом правок...")
    try:
        screenplay = await generate_screenplay(new_context, story_id=story_id)
        if story_id:
            fire(update_story(story_id, title=screenplay.get("title"),
                              screenplay_json=json.dumps(screenplay, ensure_ascii=False)))
        await status.delete()
        await _show_story(message, state, screenplay)
    except Exception as e:
        logger.error("Edit failed: %s", e, exc_info=True)
        if story_id:
            fire(log_error(story_id=story_id, phase="edit",
                           error_type=type(e).__name__, error_message=str(e),
                           traceback_str=tb_mod.format_exc()))
        await status.edit_text(f"😔 Ошибка: {str(e)[:200]}", reply_markup=main_menu())
        await state.clear()


# ── 6. Regenerate ──
@router.callback_query(F.data == "regenerate_story")
async def on_regenerate(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    story_id = data.get("db_story_id")
    if story_id:
        fire(save_revision(story_id, revision_type="regenerate", full_context=data.get("context", "")))

    status = await callback.message.answer("🔄 Сочиняю новую версию...")
    await callback.answer()
    try:
        screenplay = await generate_screenplay(data.get("context", ""), story_id=story_id)
        if story_id:
            fire(update_story(story_id, title=screenplay.get("title"),
                              screenplay_json=json.dumps(screenplay, ensure_ascii=False)))
        await status.delete()
        await _show_story(callback.message, state, screenplay)
    except Exception as e:
        logger.error("Regenerate failed: %s", e, exc_info=True)
        if story_id:
            fire(log_error(story_id=story_id, phase="regenerate",
                           error_type=type(e).__name__, error_message=str(e),
                           traceback_str=tb_mod.format_exc()))
        await status.edit_text(f"😔 Ошибка: {str(e)[:200]}", reply_markup=main_menu())


# ── 7. "Озвучить" → ask for photo ──
@router.callback_query(F.data == "generate")
async def on_generate_ask_photo(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "🖼 <b>Хотите добавить иллюстрации?</b>\n\n"
        "Отправьте <b>1-3 фото ребёнка</b> (одного, без других людей) — "
        "и он станет главным героем на картинках к сказке!\n\n"
        "📌 Чем больше разных фото — тем точнее будет сходство.\n"
        "Когда закончите, нажмите <b>«Готово»</b>.\n\n"
        "Или нажмите кнопку ниже, чтобы получить сказку без иллюстраций.",
        reply_markup=photos_done(),
        parse_mode="HTML",
    )
    await state.set_state(CreateFairyTale.waiting_photo)
    await callback.answer()


# ── 8a. Receive photo → collect into list ──
@router.message(CreateFairyTale.waiting_photo, F.photo)
async def on_photo_received(message: types.Message, state: FSMContext, bot: Bot):
    # Download the highest resolution photo and save to disk (not memory)
    photo = message.photo[-1]  # Last = largest
    file = await bot.get_file(photo.file_id)
    buf = BytesIO()
    await bot.download_file(file.file_path, buf)
    photo_bytes = buf.getvalue()

    # Save to temp file on disk
    import uuid
    photos_dir = settings.media_dir / "_photos"
    photos_dir.mkdir(parents=True, exist_ok=True)
    photo_path = photos_dir / f"{uuid.uuid4().hex}.jpg"
    photo_path.write_bytes(photo_bytes)

    data = await state.get_data()
    photo_paths = data.get("reference_photo_paths", [])
    photo_paths.append(str(photo_path))
    await state.update_data(reference_photo_paths=photo_paths)

    count = len(photo_paths)
    if count >= 3:
        await message.answer(f"📸 Отлично, {count} фото! Начинаю генерацию.")
        await _start_generation(message, state)
    else:
        await message.answer(
            f"📸 Фото {count} получено! Отправьте ещё или нажмите «Готово».",
            reply_markup=photos_done(),
        )


# ── 8b. Photos done → start generation ──
@router.callback_query(F.data == "photos_done")
async def on_photos_done(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await _start_generation(callback.message, state)


# ── 8c. Skip photo → start generation without illustrations ──
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
    story_id = data.get("db_story_id")

    # Load photos from disk paths (not base64 in memory)
    photo_paths = data.get("reference_photo_paths", [])
    reference_photos = []
    photo_b64 = None
    for p in photo_paths:
        from pathlib import Path
        pp = Path(p)
        if pp.exists():
            b64 = base64.b64encode(pp.read_bytes()).decode("ascii")
            reference_photos.append(b64)
            if photo_b64 is None:
                photo_b64 = b64

    # Update story with photo info
    if story_id:
        fire(update_story(story_id,
                          status="generating",
                          has_photo=bool(photo_b64),
                          photo_count=len(reference_photos)))

    # Magic wand sticker while generating
    from db.config_manager import cfg
    sticker_id = await cfg.get("ui.sticker_generation",
                                "CAACAgEAAxUAAWnUJVEkOcUGvclrW1NRjLNvU-L_AAJwBAAChoMgREmYf7NqHL4KOwQ")
    await message.answer_sticker(sticker_id)
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
            f"🎨 Рисую иллюстрации и собираю видео...",
            parse_mode="HTML",
        )

        audio_file = FSInputFile(audio_info["file_path"], filename=f"{audio_info['title']}.mp3")
        await message.answer_audio(
            audio=audio_file,
            title=audio_info["title"],
            performer=await cfg.get("ui.audio_performer", "Сказка на ночь"),
            caption="🎧 Включайте — видеосказка скоро будет готова!",
        )
        audio_sent = True

        # Save audio to DB
        if story_id:
            file_size = os.path.getsize(audio_info["file_path"]) if os.path.exists(audio_info["file_path"]) else None
            fire(save_media_file(story_id, file_type="audio", file_path=audio_info["file_path"],
                                 file_size=file_size, duration_sec=audio_info["duration"],
                                 mime_type="audio/mpeg"))

    try:
        result = await generate_fairytale(
            context=context,
            screenplay=screenplay,
            reference_photo_b64=photo_b64,
            reference_photos=reference_photos,
            on_status=on_status,
            on_audio_ready=on_audio_ready,
            story_id=story_id,
        )

        # Update story with results
        if story_id:
            from datetime import datetime, timezone
            fire(update_story(story_id,
                              order_id=result.get("order_id"),
                              title=result.get("title"),
                              duration_sec=result.get("duration"),
                              segments_count=result.get("segments_count"),
                              illustrations_count=len(result.get("illustrations", [])),
                              has_video=bool(result.get("video_path")),
                              status="completed",
                              completed_at=datetime.now(timezone.utc)))

            # Save video to DB
            if result.get("video_path"):
                vsize = os.path.getsize(result["video_path"]) if os.path.exists(result["video_path"]) else None
                fire(save_media_file(story_id, file_type="video", file_path=result["video_path"],
                                     file_size=vsize, duration_sec=result.get("duration"),
                                     width=1920, height=1080, mime_type="video/mp4"))

            # Save illustrations to DB
            for idx, img_path in enumerate(result.get("illustrations", [])):
                if img_path:
                    isize = os.path.getsize(img_path) if os.path.exists(img_path) else None
                    fire(save_media_file(story_id, file_type="illustration", file_path=img_path,
                                         file_size=isize, scene_index=idx, mime_type="image/png"))

        # Update status
        try:
            await status_msg.edit_text("✅ <b>Сказка готова!</b>", parse_mode="HTML")
        except Exception:
            pass

        # Fallback: send MP3 if callback didn't fire
        if not audio_sent:
            audio_file = FSInputFile(result["file_path"], filename=f"{result['title']}.mp3")
            await message.answer_audio(
                audio=audio_file,
                title=result["title"],
                performer=await cfg.get("ui.audio_performer", "Сказка на ночь"),
            )

        # Send MP4 video at the end
        video_path = result.get("video_path")
        if video_path:
            video_file = FSInputFile(video_path, filename=f"{result['title']}.mp4")
            await message.answer_video(
                video=video_file,
                caption=f"🎬 Полная видеосказка «{result['title']}»",
                duration=int(result["duration"]),
                width=1920,
                height=1080,
            )

        await message.answer("Как вам сказка?", reply_markup=feedback())

    except Exception as e:
        logger.error("Generation failed: %s", e, exc_info=True)
        if story_id:
            fire(update_story(story_id, status="failed", error_message=str(e)[:500]))
            fire(log_error(story_id=story_id, phase="generation",
                           error_type=type(e).__name__, error_message=str(e),
                           traceback_str=tb_mod.format_exc()))
        await status_msg.edit_text(
            f"😔 Не удалось создать сказку: {str(e)[:200]}\nПопробуйте ещё раз!",
            reply_markup=main_menu(),
        )
        await state.clear()


# ── 9. Feedback ──
@router.callback_query(F.data.startswith("fb_"))
async def on_feedback(callback: types.CallbackQuery, state: FSMContext):
    fb_type = callback.data.replace("fb_", "")
    labels = {"love": "❤️", "ok": "👍", "bad": "👎"}
    label = labels.get(fb_type, "?")
    logger.info("Feedback from user %d: %s", callback.from_user.id, fb_type)

    # Save feedback — try to get story_id from state (may be cleared already)
    data = await state.get_data()
    story_id = data.get("db_story_id")
    if story_id:
        fire(save_feedback(story_id, fb_type))

    await state.clear()
    await callback.message.edit_text(
        f"Спасибо за отзыв {label}!\n\nХотите ещё сказку?",
        reply_markup=main_menu(),
    )
    await callback.answer("Спасибо!")
