# -*- coding: utf-8 -*-
"""Full fairy tale generation pipeline: topic → MP3 + illustrations."""

import asyncio
import logging
import shutil
import uuid
from pathlib import Path
from typing import Callable, Awaitable

from bot.config import settings
from engine.llm_client import generate_screenplay
from engine.voice_pool import pick_voice, VoiceProfile
from engine.story_parser import build_tagged_text, AMBIENT_MAP
from engine.tts_client import synthesize_batch
from engine.audio_mixer import mix_with_ambient, concat_segments, get_duration, create_video
from engine.image_generator import generate_illustrations_batch

logger = logging.getLogger(__name__)


async def generate_fairytale(
    context: str,
    screenplay: dict | None = None,
    reference_photo_b64: str | None = None,
    reference_photos: list[str] | None = None,
    on_status: Callable[[str], Awaitable[None]] | None = None,
    on_audio_ready: Callable[[dict], Awaitable[None]] | None = None,
    on_illustration_ready: Callable[[int, str], Awaitable[None]] | None = None,
    story_id: int | None = None,
) -> dict:
    """Generate a complete fairy tale: MP3 audio + illustrations.

    Args:
        context: User's topic + child info.
        screenplay: Pre-generated screenplay dict.
        reference_photo_b64: Base64-encoded child photo (primary) for illustrations.
        reference_photos: List of all reference photos (for multi-angle face matching).
        on_status: Callback for status updates.
        on_audio_ready: Callback fired as soon as MP3 is mixed, before illustrations.
        on_illustration_ready: Callback fired for each illustration. Receives (index, file_path).
            Receives dict with: title, file_path, duration, segments_count.

    Returns:
        Dict with: title, file_path, duration, segments_count, script, illustrations.
    """
    order_id = uuid.uuid4().hex[:12]
    work_dir = settings.media_dir / order_id
    segments_dir = work_dir / "segments"
    illustrations_dir = work_dir / "illustrations"
    segments_dir.mkdir(parents=True, exist_ok=True)
    illustrations_dir.mkdir(parents=True, exist_ok=True)
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

            # Log to DB
            if story_id:
                from db.database import save_voice_assignment, fire
                fire(save_voice_assignment(
                    story_id=story_id, character_id=char["id"], character_name=char["name"],
                    voice_id=voice.voice_id, voice_name=voice.name,
                    gender=char.get("gender"), age=char.get("age"), role=char.get("role"),
                ))

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

        # ── Step 4: TTS + Illustrations in parallel ──
        await status("🎙 Озвучиваю сказку...")

        # Start TTS
        tts_task = asyncio.create_task(
            synthesize_batch(tts_requests, max_concurrent=settings.max_concurrent_tts,
                             story_id=story_id)
        )

        # Start illustrations (if photo provided or generate without face)
        illustration_paths: list[str] = []
        scene_durations_list: list[float] = []
        audio_ready_event = asyncio.Event()

        async def _on_img_ready(idx: int, img_bytes: bytes):
            img_path = illustrations_dir / f"scene_{idx + 1}.png"
            img_path.write_bytes(img_bytes)
            illustration_paths.append(str(img_path))
            # Wait until MP3 has been sent before delivering illustrations
            await audio_ready_event.wait()
            if on_illustration_ready:
                await on_illustration_ready(idx, str(img_path))

        img_task = asyncio.create_task(
            generate_illustrations_batch(
                screenplay=screenplay,
                reference_photo_b64=reference_photo_b64,
                reference_photos=reference_photos,
                on_progress=status,
                story_id=story_id,
                on_illustration_ready=_on_img_ready,
            )
        )

        # Wait for TTS
        audio_chunks = await tts_task

        # ── Step 5: Save segments + measure durations for timecodes ──
        await status("🎵 Финальное сведение...")
        seg_files = []
        seg_durations = []  # duration of each segment in seconds
        seg_indices = []    # original segment index

        seg_char_ids = []  # character_id for each saved segment (for pause logic)

        for i, audio in enumerate(audio_chunks):
            if audio is None:
                continue
            seg_path = segments_dir / f"seg_{i:02d}.mp3"
            seg_path.write_bytes(audio)
            seg_files.append(seg_path)
            seg_indices.append(i)
            seg_char_ids.append(segments[i]["character_id"])

        # Measure each segment duration for scene timecodes
        for seg_path in seg_files:
            dur = await get_duration(seg_path)
            seg_durations.append(dur)

        dry_path = work_dir / "dry.mp3"
        await concat_segments(seg_files, dry_path, character_ids=seg_char_ids)

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

        # ── Notify: audio is ready, deliver MP3 immediately ──
        if on_audio_ready:
            await on_audio_ready({
                "title": title,
                "file_path": str(final_path),
                "duration": duration,
                "segments_count": len(seg_files),
            })
        audio_ready_event.set()

        # ── Step 7: Wait for illustrations ──
        result_scenes = []
        try:
            img_results, result_scenes = await img_task
            logger.info("Illustrations: %d/%d saved", len(illustration_paths), len(img_results))
        except Exception as e:
            logger.warning("Illustrations failed: %s, continuing without them", e, exc_info=True)

        logger.info("Fairy tale audio complete: '%s', %.1fs, %d illustrations", title, duration, len(illustration_paths))

        # ── Step 8: Create MP4 video with scene-synced timecodes ──
        video_path = None
        if illustration_paths:
            await status("🎬 Собираю видео...")
            mp4_path = work_dir / "fairytale.mp4"

            # Calculate per-scene durations from segment timecodes
            n_scenes = len(illustration_paths)
            n_segs = len(seg_durations)

            # Try to use segment_start/segment_end from scene split
            scene_data = result_scenes
            has_segment_ranges = (
                scene_data
                and len(scene_data) >= n_scenes
                and all("segment_start" in s and "segment_end" in s for s in scene_data[:n_scenes])
            )

            if has_segment_ranges:
                for sc_idx in range(n_scenes):
                    s_start = scene_data[sc_idx].get("segment_start", 0)
                    s_end = scene_data[sc_idx].get("segment_end", n_segs)
                    s_start = max(0, min(s_start, n_segs))
                    s_end = max(s_start, min(s_end, n_segs))
                    scene_dur = sum(seg_durations[s_start:s_end]) if s_start < s_end else seg_durations[0]
                    scene_durations_list.append(scene_dur)
                logger.info("Using LLM segment ranges for scene timecodes")
            else:
                # Fallback: distribute evenly
                segs_per_scene = max(1, n_segs // n_scenes)
                for sc_idx in range(n_scenes):
                    start = sc_idx * segs_per_scene
                    end = (sc_idx + 1) * segs_per_scene if sc_idx < n_scenes - 1 else n_segs
                    scene_dur = sum(seg_durations[start:end])
                    scene_durations_list.append(scene_dur)
                logger.info("Using even distribution for scene timecodes (no segment ranges)")

            logger.info("Scene timecodes: %s (total: %.1fs)", scene_durations_list, sum(scene_durations_list))

            try:
                await create_video(final_path, illustration_paths, mp4_path, durations=scene_durations_list or None)
                video_path = str(mp4_path)
                logger.info("Video created: %s", video_path)
            except Exception as e:
                logger.warning("Video creation failed: %s, delivering audio only", e)

        # Cleanup temp files
        shutil.rmtree(segments_dir, ignore_errors=True)
        dry_path.unlink(missing_ok=True)

        # Build scene start times (cumulative) for timed delivery
        scene_start_times = []
        if illustration_paths and scene_durations_list:
            cumulative = 0.0
            for sd in scene_durations_list:
                scene_start_times.append(cumulative)
                cumulative += sd

        return {
            "title": title,
            "file_path": str(final_path),
            "video_path": video_path,
            "duration": duration,
            "segments_count": len(seg_files),
            "order_id": order_id,
            "script": screenplay,
            "illustrations": illustration_paths,
            "scene_start_times": scene_start_times,
        }

    except Exception as e:
        logger.error("Pipeline failed: %s", e, exc_info=True)
        shutil.rmtree(work_dir, ignore_errors=True)
        raise
