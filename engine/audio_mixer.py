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


async def concat_segments(
    segment_files: list[str | Path],
    output_path: str | Path,
) -> None:
    """Concatenate multiple MP3 files into one."""
    workdir = Path(segment_files[0]).parent
    filelist = workdir / "filelist.txt"

    with open(filelist, "w") as f:
        for sf in segment_files:
            f.write(f"file '{os.path.abspath(sf)}'\n")

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
) -> None:
    """Create MP4 video: slideshow of images synced to audio duration.

    Each image is shown for an equal portion of the audio duration,
    with a smooth crossfade transition between them.
    """
    audio_dur = await get_duration(audio_path)
    n = len(image_paths)
    if n == 0:
        raise ValueError("No images to create video from")

    # Duration per image
    dur_per_img = audio_dur / n

    # Build ffmpeg filter for crossfade slideshow
    # Each image: scale to 1920x1080, show for dur_per_img seconds
    inputs = []
    filter_parts = []

    for i, img in enumerate(image_paths):
        inputs.extend(["-loop", "1", "-t", f"{dur_per_img:.2f}", "-i", str(img)])
        filter_parts.append(f"[{i}:v]scale=1920:1080:force_original_aspect_ratio=decrease,"
                           f"pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=black,"
                           f"setsar=1[v{i}]")

    # Concatenate all video streams
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
        "-crf", "23",
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
