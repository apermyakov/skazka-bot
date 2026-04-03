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
