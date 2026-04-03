# -*- coding: utf-8 -*-
"""Full fairy tale generation pipeline: topic → MP3."""

import asyncio
import logging
import shutil
import time
import uuid
from pathlib import Path
from typing import Callable, Awaitable

from bot.config import settings
from engine.llm_client import generate_screenplay
from engine.voice_pool import pick_voice, VoiceProfile
from engine.story_parser import build_tagged_text, AMBIENT_MAP
from engine.tts_client import synthesize_batch
from engine.audio_mixer import mix_with_ambient, concat_segments, get_duration

logger = logging.getLogger(__name__)


async def generate_fairytale(
    context: str,
    screenplay: dict | None = None,
    on_status: Callable[[str], Awaitable[None]] | None = None,
) -> dict:
    """Generate a complete fairy tale MP3.

    Args:
        context: User's topic + child info (used if screenplay is None).
        screenplay: Pre-generated screenplay dict. If None, generates new one.
        on_status: Callback for short status updates.

    Returns:
        Dict with: title, file_path, duration, segments_count, script.
    """
    order_id = uuid.uuid4().hex[:12]
    work_dir = settings.media_dir / order_id
    segments_dir = work_dir / "segments"
    segments_dir.mkdir(parents=True, exist_ok=True)
    final_path = work_dir / "final.mp3"

    async def status(msg: str):
        if on_status:
            await on_status(msg)

    try:
        # ── Step 1: Get or generate screenplay ──
        if screenplay is None:
            await status("📝 Сочиняю сказку...")
            screenplay = await generate_screenplay(context)

        title = screenplay["title"]
        segments = screenplay["segments"]
        scenes = screenplay.get("scenes", [])

        logger.info("Screenplay: '%s', %d segments", title, len(segments))

        # ── Step 2: Assign voices ──
        voice_map: dict[str, VoiceProfile] = {}
        assigned: dict[str, str] = {}

        for char in screenplay["characters"]:
            voice = pick_voice(
                gender=char.get("gender", "female"),
                age=char.get("age", "middle"),
                role=char.get("role", "narrator"),
                already_used=assigned,
            )
            voice_map[char["id"]] = voice
            assigned[char["id"]] = voice.voice_id
            logger.info("Cast: %s -> %s (%s)", char["name"], voice.name, voice.voice_id)

        # ── Step 3: Build TTS requests ──
        tts_requests = []
        for seg in segments:
            char_id = seg["character_id"]
            voice = voice_map.get(char_id, voice_map.get("narrator"))
            tagged_text = build_tagged_text(
                seg["text"],
                seg.get("emotion", "neutral"),
                seg.get("pace", "normal"),
                is_narrator=(char_id == "narrator"),
            )
            tts_requests.append({
                "text": tagged_text,
                "voice_id": voice.voice_id,
                "stability": voice.default_stability,
                "similarity": voice.default_similarity,
                "style": voice.default_style,
            })

        # ── Step 4: Synthesize speech ──
        await status("🎙 Озвучиваю сказку...")

        audio_chunks = await synthesize_batch(
            tts_requests,
            max_concurrent=settings.max_concurrent_tts,
        )

        # ── Step 5: Save + Concatenate ──
        await status("🎵 Финальное сведение...")
        seg_files = []
        for i, audio in enumerate(audio_chunks):
            if audio is None:
                continue
            seg_path = segments_dir / f"seg_{i:02d}.mp3"
            seg_path.write_bytes(audio)
            seg_files.append(seg_path)

        dry_path = work_dir / "dry.mp3"
        await concat_segments(seg_files, dry_path)

        # ── Step 6: Overlay ambient ──
        assets_dir = Path(__file__).parent.parent / "assets" / "ambient_sounds"
        primary_ambient = "forest"
        if scenes:
            primary_ambient = scenes[0].get("ambient", "forest")
        ambient_name = AMBIENT_MAP.get(primary_ambient, "forest_ambience.mp3")
        ambient_path = assets_dir / ambient_name

        if ambient_path.exists():
            try:
                await mix_with_ambient(dry_path, ambient_path, final_path, ambient_vol=0.10)
            except Exception as e:
                logger.warning("Ambient mix failed: %s, using dry speech", e)
                shutil.copy2(dry_path, final_path)
        else:
            shutil.copy2(dry_path, final_path)

        duration = await get_duration(final_path)
        file_size = final_path.stat().st_size

        logger.info("Fairy tale complete: '%s', %.1fs, %d bytes", title, duration, file_size)

        # Cleanup
        shutil.rmtree(segments_dir, ignore_errors=True)
        dry_path.unlink(missing_ok=True)

        return {
            "title": title,
            "file_path": str(final_path),
            "duration": duration,
            "segments_count": len(seg_files),
            "order_id": order_id,
            "script": screenplay,
        }

    except Exception as e:
        logger.error("Pipeline failed: %s", e, exc_info=True)
        shutil.rmtree(work_dir, ignore_errors=True)
        raise
