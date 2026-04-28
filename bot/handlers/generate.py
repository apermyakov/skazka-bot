# -*- coding: utf-8 -*-
"""Generation pipeline: photo input, audio+illustration generation, delivery, feedback."""

import base64
import json
import logging
import os
import traceback as tb_mod
from io import BytesIO

from aiogram import Router, types, F, Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import FSInputFile

from bot.config import settings
from bot.states.create import CreateFairyTale
from bot.keyboards.inline import skip_photo, photos_done, feedback, main_menu
from engine.pipeline import generate_fairytale
from engine.llm_client import convert_to_screenplay
from bot.notify import notify_error, notify_story_complete
from db.database import (
    update_story, log_error, save_feedback,
    save_media_file, fire,
)

from bot.handlers.utils import (
    _msg, _dismiss, MAX_PHOTO_SIZE,
)

logger = logging.getLogger(__name__)
router = Router()


# -- 7. "Озвучить (темп)" -> ask for photo --
@router.callback_query(F.data.startswith("generate:"))
async def on_generate_ask_photo(callback: types.CallbackQuery, state: FSMContext):
    speed_label = callback.data.split(":", 1)[1]  # slow | normal | fast
    await state.update_data(speed=speed_label)
    await callback.message.answer(
        "📸 Отправьте <b>фото ребёнка</b> для иллюстраций\n"
        "<i>(одного, без других людей)</i>",
        reply_markup=photos_done(),
        parse_mode="HTML",
    )
    await state.set_state(CreateFairyTale.waiting_photo)
    await _dismiss(callback)


# -- 8a. Receive photo -> save and start generation --
@router.message(CreateFairyTale.waiting_photo, F.photo)
async def on_photo_received(message: types.Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    if data.get("_busy"):
        await message.reply(await _msg("msg.photo_duplicate", "⚠️ Фото уже загружено, используем первое."))
        return
    await state.update_data(_busy=True)
    photo = message.photo[-1]
    if photo.file_size and photo.file_size > MAX_PHOTO_SIZE:
        await message.answer(f"⚠️ Фото слишком большое (макс {MAX_PHOTO_SIZE // 1024 // 1024}МБ).")
        return
    file = await bot.get_file(photo.file_id)
    buf = BytesIO()
    await bot.download_file(file.file_path, buf)

    import uuid
    photos_dir = settings.media_dir / "_photos"
    photos_dir.mkdir(parents=True, exist_ok=True)
    photo_path = photos_dir / f"{uuid.uuid4().hex}.jpg"
    photo_path.write_bytes(buf.getvalue())

    await state.update_data(reference_photo_paths=[str(photo_path)])
    await message.reply(await _msg("msg.photo_accepted", "📸 Это фото будет использовано для иллюстраций"))
    await _start_generation(message, state)


# -- 8a-bis. Receive photo sent as document (file) --
@router.message(CreateFairyTale.waiting_photo, F.document)
async def on_photo_document_received(message: types.Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    if data.get("_busy"):
        await message.reply(await _msg("msg.photo_duplicate", "⚠️ Фото уже загружено, используем первое."))
        return
    await state.update_data(_busy=True)
    doc = message.document
    if not doc.mime_type or not doc.mime_type.startswith("image/"):
        await message.answer(await _msg("msg.photo_not_image", "Отправьте фото ребёнка (изображение)."))
        return
    if doc.mime_type and doc.mime_type not in ("image/jpeg", "image/png"):
        await message.answer(await _msg("msg.photo_wrong_format", "⚠️ Поддерживаются только JPEG и PNG."))
        return
    if doc.file_size and doc.file_size > MAX_PHOTO_SIZE:
        await message.answer(f"⚠️ Файл слишком большой (макс {MAX_PHOTO_SIZE // 1024 // 1024}МБ).")
        return
    file = await bot.get_file(doc.file_id)
    buf = BytesIO()
    await bot.download_file(file.file_path, buf)

    import uuid
    photos_dir = settings.media_dir / "_photos"
    photos_dir.mkdir(parents=True, exist_ok=True)
    photo_path = photos_dir / f"{uuid.uuid4().hex}.jpg"
    photo_path.write_bytes(buf.getvalue())

    await state.update_data(reference_photo_paths=[str(photo_path)])
    await message.reply(await _msg("msg.photo_accepted", "📸 Это фото будет использовано для иллюстраций"))
    await _start_generation(message, state)


# -- 8b. Photos done -> start generation --
@router.callback_query(F.data == "photos_done")
async def on_photos_done(callback: types.CallbackQuery, state: FSMContext):
    await _dismiss(callback)
    await _start_generation(callback.message, state)


# -- 8c. Skip photo -> start generation without illustrations --
@router.callback_query(F.data == "skip_photo")
async def on_skip_photo(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(reference_photo_b64=None)
    await _dismiss(callback)
    await _start_generation(callback.message, state)


async def _start_generation(message: types.Message, state: FSMContext):
    """Run the full pipeline: convert text -> screenplay JSON -> audio + illustrations."""
    await state.set_state(CreateFairyTale.generating)
    data = await state.get_data()
    context = data["context"]
    story_title = data.get("story_title", "Сказка")
    story_text = data.get("story_text", "")
    story_id = data.get("db_story_id")

    # Show sticker + status immediately
    from db.config_manager import cfg
    sticker_id = await cfg.get("ui.sticker_generation",
                                "CAACAgEAAxUAAWnUJVEkOcUGvclrW1NRjLNvU-L_AAJwBAAChoMgREmYf7NqHL4KOwQ")
    sticker_msg = await message.answer_sticker(sticker_id)
    status_msg = await message.answer(
        "🎙 <b>Создаю сказку...</b>\n\n"
        "⏳ Подготовка озвучки...",
        parse_mode="HTML",
    )

    # Step 2: Convert plain text -> structured screenplay JSON
    try:
        screenplay = await convert_to_screenplay(story_title, story_text, story_id=story_id)
        if story_id:
            fire(update_story(story_id,
                              screenplay_json=json.dumps(screenplay, ensure_ascii=False)))
    except Exception as e:
        logger.error("Screenplay conversion failed: %s", e, exc_info=True)
        fire(notify_error(e, user_id=message.chat.id, phase="screenplay_convert"))
        try:
            await sticker_msg.delete()
        except Exception:
            pass
        await status_msg.edit_text(f"😔 Ошибка подготовки озвучки: {str(e)[:200]}", reply_markup=main_menu())
        await state.clear()
        return

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

    # Update status message
    try:
        await status_msg.edit_text(
            "🎙 <b>Создаю сказку...</b>\n\n"
            "✅ Подготовка\n"
            "⏳ Озвучиваю текст...",
            parse_mode="HTML",
        )
    except Exception:
        pass

    async def on_status(msg: str):
        try:
            await status_msg.edit_text(msg, parse_mode="HTML")
        except Exception:
            pass

    async def on_audio_ready(audio_info: dict):
        try:
            await status_msg.edit_text(
                f"🎙 <b>Создаю сказку...</b>\n\n"
                f"✅ Озвучка\n"
                f"🎨 Рисую иллюстрации...",
                parse_mode="HTML",
            )
        except Exception:
            pass

        # Save audio to DB
        if story_id:
            file_size = os.path.getsize(audio_info["file_path"]) if os.path.exists(audio_info["file_path"]) else None
            fire(save_media_file(story_id, file_type="audio", file_path=audio_info["file_path"],
                                 file_size=file_size, duration_sec=audio_info["duration"],
                                 mime_type="audio/mpeg"))

    speed = data.get("speed", "slow")
    tempo_key = {"slow": "audio.tempo_slow", "normal": "audio.tempo_normal", "fast": "audio.tempo_fast"}.get(speed, "audio.tempo_slow")
    tempo = float(await cfg.get(tempo_key, 1.0 if speed == "slow" else (1.15 if speed == "normal" else 1.30)))
    logger.info("Pipeline tempo: speed=%s -> %s=%.2f", speed, tempo_key, tempo)

    try:
        result = await generate_fairytale(
            context=context,
            screenplay=screenplay,
            reference_photo_b64=photo_b64,
            reference_photos=reference_photos,
            on_status=on_status,
            on_audio_ready=on_audio_ready,
            story_id=story_id,
            tempo=tempo,
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

        # Send only MP4 video
        video_path = result.get("video_path")
        if video_path:
            try:
                await sticker_msg.delete()
            except Exception:
                pass
            try:
                await status_msg.edit_text(
                    f"✅ <b>{result['title']}</b>",
                    parse_mode="HTML",
                )
            except Exception:
                pass

            video_file = FSInputFile(video_path, filename=f"{result['title']}.mp4")
            try:
                await message.answer_video(
                    video=video_file,
                    caption=f"🎬 «{result['title']}»",
                    duration=int(result["duration"]),
                    width=1920,
                    height=1080,
                )
            except Exception as ve:
                logger.warning("Video send failed (%s), sending link instead", ve)
                # Video too large for Telegram -- send direct link
                relative = video_path
                if relative.startswith("/app/"):
                    relative = relative[5:]
                if relative.startswith("media/"):
                    relative = relative[6:]
                video_url = f"{await cfg.get('media_base_url', 'http://95.216.117.49/media')}/{relative}"
                # Send MP3 + download link
                audio_file = FSInputFile(result["file_path"], filename=f"{result['title']}.mp3")
                await message.answer_audio(
                    audio=audio_file,
                    title=result["title"],
                    performer=await cfg.get("ui.audio_performer", "Сказка на ночь"),
                )
                await message.answer(
                    f"🎬 <a href=\"{video_url}\">Скачать видеосказку</a>",
                    parse_mode="HTML",
                )
        else:
            # Fallback: no video -- send MP3
            try:
                await sticker_msg.delete()
            except Exception:
                pass
            try:
                await status_msg.edit_text("✅ <b>Сказка готова!</b>", parse_mode="HTML")
            except Exception:
                pass
            audio_file = FSInputFile(result["file_path"], filename=f"{result['title']}.mp3")
            await message.answer_audio(
                audio=audio_file,
                title=result["title"],
                performer=await cfg.get("ui.audio_performer", "Сказка на ночь"),
            )

        await message.answer(await _msg("msg.feedback_prompt", "Как вам сказка?"), reply_markup=feedback())

        # Notify admin with media links
        order_id = result.get("order_id", "")
        base_url = await cfg.get("media_base_url", "http://95.216.117.49/media")
        v_url = f"{base_url}/{order_id}/fairytale.mp4" if result.get("video_path") else None
        a_url = f"{base_url}/{order_id}/final.mp3"
        fire(notify_story_complete(
            user_id=message.chat.id,
            username=message.chat.username if hasattr(message.chat, 'username') else None,
            title=result.get("title"),
            duration=result.get("duration"),
            video_url=v_url,
            audio_url=a_url,
        ))

    except Exception as e:
        logger.error("Generation failed: %s", e, exc_info=True)
        fire(notify_error(e, user_id=message.chat.id, phase="generation",
                          context=context[:200] if context else None))
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


# -- 9. Feedback --
@router.callback_query(F.data.startswith("fb_"))
async def on_feedback(callback: types.CallbackQuery, state: FSMContext):
    fb_type = callback.data.replace("fb_", "")
    labels = {"love": "❤️", "ok": "👍", "bad": "👎"}
    label = labels.get(fb_type, "?")
    logger.info("Feedback from user %d: %s", callback.from_user.id, fb_type)

    # Save feedback -- try to get story_id from state (may be cleared already)
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
