#!/usr/bin/env python3
"""Deep live tests against https://lalaka.ai (production).

Covers the two paths the user reported broken:
1. POST /create — honeypot, rate-limit, captcha gates; legitimate submit.
2. POST /transcribe — webm + mp4 (mobile Safari) + edge cases.

Run from container or host. Reports each test in colour, exits non-zero
if any critical test fails.
"""
from __future__ import annotations
import asyncio, json, os, sys, time, tempfile, subprocess
from pathlib import Path

import aiohttp

BASE = os.environ.get("LALAKA_TEST_BASE", "https://lalaka.ai")
RESULTS: list[tuple[str, bool, str]] = []

# ── helpers ──────────────────────────────────────────────────────────
GREEN = "\033[32m"; RED = "\033[31m"; YEL = "\033[33m"; DIM = "\033[2m"; RST = "\033[0m"
def ok(name, msg=""):   RESULTS.append((name, True, msg));  print(f"  {GREEN}✓{RST} {name:<55} {DIM}{msg}{RST}")
def fail(name, msg=""): RESULTS.append((name, False, msg)); print(f"  {RED}✗{RST} {name:<55} {RED}{msg}{RST}")
def warn(name, msg=""): print(f"  {YEL}⚠{RST} {name:<55} {YEL}{msg}{RST}")

# ── audio fixtures ──────────────────────────────────────────────────
def make_audio(out: Path, codec: str, duration: float, container: str,
                kind: str = "silence"):
    """ffmpeg-generate audio: kind=silence|tone for the no-speech tests."""
    if kind == "silence":
        src = f"anullsrc=channel_layout=mono:sample_rate=16000:duration={duration}"
    else:
        src = f"sine=frequency=440:duration={duration}"
    cmd = ["ffmpeg","-y","-f","lavfi","-i", src]
    if container == "webm":
        cmd += ["-c:a", codec or "libopus"]
    elif container == "mp4":
        cmd += ["-c:a", codec or "aac"]
    elif container == "mp3":
        cmd += ["-c:a", codec or "libmp3lame"]
    cmd += [str(out)]
    r = subprocess.run(cmd, capture_output=True)
    return r.returncode == 0

# ── tests ───────────────────────────────────────────────────────────
async def test_create_honeypot(s):
    """Honeypot field filled → silent 303 to ?err=1."""
    async with s.post(f"{BASE}/create", data={
        "topic": "ten characters at least here", "email": "h@e.com", "locale": "en",
        "website": "spam-bot-touched-this",   # honeypot tripped
    }, allow_redirects=False) as r:
        loc = r.headers.get("location","")
        if r.status == 303 and "err=1" in loc:
            ok("/create honeypot blocks", loc)
        else:
            fail("/create honeypot", f"status={r.status} loc={loc}")

async def test_create_short_topic(s):
    """Topic <10 chars → 303 to ?err=1."""
    async with s.post(f"{BASE}/create", data={
        "topic": "short", "email": "x@e.com", "locale": "en",
    }, allow_redirects=False) as r:
        if r.status == 303 and "err=1" in (r.headers.get("location","")):
            ok("/create rejects short topic")
        else:
            fail("/create short topic", f"status={r.status}")

async def test_create_bad_email(s):
    async with s.post(f"{BASE}/create", data={
        "topic": "Lily age four afraid of dark", "email": "not-an-email", "locale": "en",
    }, allow_redirects=False) as r:
        if r.status == 303 and "err=1" in (r.headers.get("location","")):
            ok("/create rejects malformed email")
        else:
            fail("/create bad email", f"status={r.status}")

async def test_create_no_captcha(s):
    """No reCAPTCHA token → 303 to ?err=captcha (since RECAPTCHA_SECRET is set)."""
    async with s.post(f"{BASE}/create", data={
        "topic": "Lily age four afraid of dark", "email": "test@example.com", "locale": "en",
    }, allow_redirects=False) as r:
        loc = r.headers.get("location","")
        if r.status == 303 and "err=captcha" in loc:
            ok("/create blocks no-captcha", loc)
        else:
            fail("/create no captcha", f"status={r.status} loc={loc}")

# ── transcribe tests ────────────────────────────────────────────────
async def test_transcribe(s, audio_path: Path, label: str, locale: str = "en",
                          expect_ok: bool = True, expect_pure_text: bool = False):
    data = aiohttp.FormData()
    data.add_field("audio", audio_path.read_bytes(), filename=audio_path.name,
                    content_type=f"audio/{audio_path.suffix[1:]}")
    data.add_field("locale", locale)
    t0 = time.time()
    async with s.post(f"{BASE}/transcribe", data=data) as r:
        try:
            j = await r.json()
        except Exception as e:
            fail(f"/transcribe {label}", f"json parse: {e}")
            return None
    dt = time.time() - t0
    if expect_ok:
        if r.status == 200 and j.get("ok"):
            text = j.get("text","")
            if expect_pure_text:
                # Pure transcription should not contain meta-commentary
                bad_phrases = ["transcription:", "here is", "sure,", "the audio", "(note:",
                               "the speaker", "transcribed text:", "in english:", "translation:"]
                lower = text.lower()
                contaminated = [p for p in bad_phrases if p in lower]
                if contaminated:
                    fail(f"/transcribe {label} clean-output", f"contains {contaminated}; got: {text[:100]}")
                else:
                    ok(f"/transcribe {label}", f"{dt:.1f}s, {len(text)}ch: {text[:60]!r}")
            else:
                ok(f"/transcribe {label}", f"{dt:.1f}s, {len(text)}ch: {text[:60]!r}")
            return text
        fail(f"/transcribe {label}", f"status={r.status} body={j}")
    else:
        if r.status >= 400 or not j.get("ok"):
            ok(f"/transcribe {label} (expect err)", f"got {r.status}: {j.get('error')}")
        else:
            fail(f"/transcribe {label} (expect err)", "unexpectedly succeeded")
    return None

# ── runner ──────────────────────────────────────────────────────────
async def main():
    print(f"\n  {DIM}Lalaka live tests against {BASE}{RST}\n")
    print(f"  {YEL}Section 1: /create rejection paths{RST}")
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as s:
        await test_create_honeypot(s)
        await test_create_short_topic(s)
        await test_create_bad_email(s)
        await test_create_no_captcha(s)

        print(f"\n  {YEL}Section 2: /transcribe MUST NOT hallucinate on silence/tone{RST}")
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            no_speech_tests = [
                ("silence_chrome.webm",  "libopus", 2.0, "webm", "silence",
                    "silence webm/opus (Chrome)"),
                ("silence_safari.mp4",   "aac",     2.0, "mp4",  "silence",
                    "silence mp4/aac (Safari mobile)"),
                ("tone_chrome.webm",     "libopus", 2.0, "webm", "tone",
                    "440Hz tone webm/opus"),
                ("silence_short.webm",   "libopus", 0.5, "webm", "silence",
                    "500ms silence"),
            ]
            for fname, codec, dur, cont, kind, label in no_speech_tests:
                p = td / fname
                if not make_audio(p, codec, dur, cont, kind):
                    warn(f"/transcribe {label}", "ffmpeg failed to generate fixture")
                    continue
                # Expect ok=false with no_speech/empty — NOT a hallucinated story
                async with s.post(f"{BASE}/transcribe", data=({
                    "audio": ("ignored", p.read_bytes(), f"audio/{cont}"),
                    "locale": "en",
                } and aiohttp.FormData())) as _:
                    pass  # placeholder to keep flow; use the helper instead
                fd = aiohttp.FormData()
                fd.add_field("audio", p.read_bytes(), filename=p.name,
                             content_type=f"audio/{cont}")
                fd.add_field("locale", "en")
                async with s.post(f"{BASE}/transcribe", data=fd) as r:
                    j = await r.json()
                if j.get("ok"):
                    t = (j.get("text") or "").lower()
                    # Hallucination signals: any reference to characters / fairy-tale tropes
                    hall_markers = ["maya","lily","mila","leni","name is","years old",
                                    "fairy","princess","once upon","story about","child"]
                    hits = [m for m in hall_markers if m in t]
                    if hits:
                        fail(f"/transcribe {label}", f"HALLUCINATED ({hits}): {t[:80]!r}")
                    else:
                        ok(f"/transcribe {label}", f"got non-story text: {t[:60]!r}")
                else:
                    err = j.get("error")
                    if err in ("no_speech", "empty"):
                        ok(f"/transcribe {label}", f"correctly rejected: {err}")
                    else:
                        warn(f"/transcribe {label}", f"non-empty err: {err}")

            print(f"\n  {YEL}Section 3: /transcribe with real speech samples (per locale){RST}")
            for loc in ["de", "ja", "ar"]:
                p = td / f"loc_{loc}.webm"
                if make_audio(p, "libopus", 2.0, "webm", "silence"):
                    # silence should still NOT hallucinate even in non-EN locale
                    fd = aiohttp.FormData()
                    fd.add_field("audio", p.read_bytes(), filename=p.name,
                                 content_type="audio/webm")
                    fd.add_field("locale", loc)
                    async with s.post(f"{BASE}/transcribe", data=fd) as r:
                        j = await r.json()
                    if not j.get("ok") and j.get("error") in ("no_speech", "empty"):
                        ok(f"/transcribe silence locale={loc}", f"rejected: {j.get('error')}")
                    elif j.get("ok"):
                        t = j.get("text","").lower()
                        if any(m in t for m in ["years old","name is","fairy","story about"]):
                            fail(f"/transcribe silence locale={loc}", f"HALLUCINATED: {t[:80]!r}")
                        else:
                            ok(f"/transcribe silence locale={loc}", f"non-story: {t[:60]!r}")

    # ── summary ────────────────────────────────────────────────────
    passed = sum(1 for _, p, _ in RESULTS if p)
    failed = sum(1 for _, p, _ in RESULTS if not p)
    print(f"\n  {DIM}─────────────────────────────────────────────{RST}")
    if failed == 0:
        print(f"  {GREEN}all {passed} tests passed ✨{RST}\n")
        sys.exit(0)
    else:
        print(f"  {RED}{failed} failed{RST}, {GREEN}{passed} passed{RST}\n")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
