# -*- coding: utf-8 -*-
"""Async ffmpeg wrapper for ambient mixing and concatenation."""

import asyncio
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


async def get_duration(filepath: str | Path) -> float:
    """Get audio duration in seconds via ffprobe."""
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
        "-of", "csv=p=0", str(filepath),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    return float(stdout.decode().strip())


async def mix_with_ambient(
    speech_path: str | Path,
    ambient_path: str | Path,
    output_path: str | Path,
    ambient_vol: float = 0.12,
    tail_seconds: float = 5.0,
) -> None:
    """Overlay ambient sound under speech, with a fading ambient tail.

    The ambient plays under the speech at ambient_vol, then continues
    for tail_seconds after the speech ends, fading out to silence.
    """
    dur = await get_duration(speech_path)
    total_dur = dur + tail_seconds
    fade_out_start = total_dur - tail_seconds

    filter_complex = (
        # Ambient: loop, trim to total duration (speech + tail), set volume, fade in/out
        f"[1:a]aloop=loop=-1:size=2e+09,atrim=duration={total_dur},"
        f"volume={ambient_vol},"
        f"afade=t=in:d=1.0,afade=t=out:st={fade_out_start}:d={tail_seconds}[bg];"
        # Speech: pad with silence at the end to match total duration
        f"[0:a]apad=whole_dur={total_dur}[speech];"
        # Mix speech + ambient
        f"[speech][bg]amix=inputs=2:duration=longest:dropout_transition=0[out]"
    )

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y",
        "-i", str(speech_path),
        "-i", str(ambient_path),
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-ar", "44100",
        "-b:a", "128k",
        str(output_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg mix failed: {stderr.decode()[-200:]}")


async def _generate_silence(output_path: str | Path, duration: float = 1.0) -> None:
    """Generate a silent MP3 file of given duration."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=mono",
        "-t", f"{duration:.2f}",
        "-c:a", "libmp3lame", "-b:a", "128k",
        str(output_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()


async def concat_segments(
    segment_files: list[str | Path],
    output_path: str | Path,
    character_ids: list[str] | None = None,
) -> None:
    """Concatenate MP3 files with pauses between them.

    Inserts 1.0s silence between segments of the same speaker,
    and 1.5s silence when the speaker changes (dialog transition).
    """
    workdir = Path(segment_files[0]).parent

    # Generate silence files
    short_pause = workdir / "_pause_short.mp3"
    long_pause = workdir / "_pause_long.mp3"
    await _generate_silence(short_pause, 0.7)
    await _generate_silence(long_pause, 1.3)

    filelist = workdir / "filelist.txt"

    with open(filelist, "w") as f:
        for i, sf in enumerate(segment_files):
            f.write(f"file '{os.path.abspath(sf)}'\n")

            # Add pause between segments (not after the last one)
            if i < len(segment_files) - 1:
                if character_ids and i + 1 < len(character_ids) and character_ids[i] != character_ids[i + 1]:
                    # Speaker changes → longer pause
                    f.write(f"file '{os.path.abspath(long_pause)}'\n")
                else:
                    # Same speaker → shorter pause
                    f.write(f"file '{os.path.abspath(short_pause)}'\n")

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(filelist),
        "-c:a", "libmp3lame",
        "-b:a", "128k",
        str(output_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg concat failed: {stderr.decode()[-200:]}")

    # Cleanup filelist
    filelist.unlink(missing_ok=True)


async def create_video(
    audio_path: str | Path,
    image_paths: list[str | Path],
    output_path: str | Path,
    durations: list[float] | None = None,
) -> None:
    """Create MP4 video: static slideshow of images synced to audio.

    Each image is scaled to fit 1920x1080 preserving aspect ratio.
    """
    audio_dur = await get_duration(audio_path)
    n = len(image_paths)
    if n == 0:
        raise ValueError("No images to create video from")

    if durations is None:
        durations = [audio_dur / n] * n

    inputs = []
    filter_parts = []

    for i, (img, dur) in enumerate(zip(image_paths, durations)):
        inputs.extend(["-loop", "1", "-t", f"{dur:.2f}", "-framerate", "2", "-i", str(img)])
        filter_parts.append(
            f"[{i}:v]scale=1920:1080:force_original_aspect_ratio=decrease,"
            f"pad=1920:1080:-1:-1:color=black,setsar=1,fps=2,format=yuv420p[v{i}]"
        )

    concat_inputs = "".join(f"[v{i}]" for i in range(n))
    filter_parts.append(f"{concat_inputs}concat=n={n}:v=1:a=0[video]")

    filter_complex = ";".join(filter_parts)

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-i", str(audio_path),
        "-filter_complex", filter_complex,
        "-map", "[video]",
        "-map", f"{n}:a",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-c:a", "aac",
        "-b:a", "128k",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-shortest",
        str(output_path),
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg video failed: {stderr.decode()[-300:]}")
