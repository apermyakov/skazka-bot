# -*- coding: utf-8 -*-
"""Language-filtered voice pool for Lalaka. Fetches ElevenLabs voices once,
caches for 24h, and returns voice picks filtered by target language.

Skazik's hard-coded VOICE_POOL (engine/voice_pool.py) is intentionally
untouched — that pool is auditioned for Russian and stays the source of
truth for skazik.app.

ElevenLabs voice metadata used:
- `labels.language` — older voices, BCP-47 code like "en", "de", "ja"
- `verified_languages` — v3 voices, list of {language, model_id}
- `labels.gender` / `labels.age` / `labels.descriptive` — voice character

We prefer `verified_languages` (Eleven v3-confirmed) over `labels.language`.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass

import aiohttp

logger = logging.getLogger(__name__)

ELEVENLABS_API = "https://api.elevenlabs.io"
CACHE_TTL_SEC = 86400  # 24h

# Languages we additionally enrich from the public Voice Library (shared voices).
# Boosts thin locales like ja (16 → 60+) without hurting wider locales.
SHARED_LANGS = ("en", "de", "es", "fr", "it", "pl", "pt", "tr", "ja", "ko", "ar")
SHARED_PER_LANG = 50  # top professional shared voices per language

_lock = asyncio.Lock()
_cache: dict = {"fetched_at": 0.0, "voices": []}

# Hand-curated narrator voices per locale. Native voices preferred; multi-language
# warm female fallback for under-served locales. Used as priority pick for the
# 'narrator' role during real story generation. Other characters still go through
# the broader pool via get_voices_for_locale.
CURATED_NARRATORS: dict[str, str] = {
    "en":    "hpp4J3VqNfWAUOO0d1Us",  # Bella — Professional Bright Warm
    "de":    "dCnu06FiOZma2KVNUoPZ",  # Mila Winter — Narration (expressive)
    "es":    "EXAVITQu4vr4xnSDxMaL",  # Sarah — Mature, Reassuring, Confident
    "fr":    "McVZB9hVxVSk3Equu8EH",  # Audrey (French native)
    "it":    "wJqPPQ618aTW29mptyoc",  # Ana-Rita2 (soft female, verified it)
    "pl":    "xsSg7GkDPDhaGZpbKOLn",  # Tomasz Z — Fairyland Storyteller (male)
    "pt-BR": "RVmX026jCrF5VqUvpCk0",  # Giselli — Voice Library, native BR, "calm clear voice for narration"
    "tr":    "Sm1seazb4gs7RSlUVw7c",  # Anika — Animated and Friendly
    "ja":    "HQ1o7gECNyaEC0RiqY4w",  # Sui — Voice Library, female JP native, "perfect for audiobooks, bedtime"
    "ko":    "uyVNoMrnUku1dZyVEXwD",  # Anna Kim (Korean native female)
    "ar":    "3AH0h1SXwwhE8vUUWuQW",  # Maryam — Voice Library, Kuwaiti soft warm storytelling
}


@dataclass(frozen=True)
class IntlVoice:
    voice_id: str
    name: str
    languages: tuple[str, ...]   # BCP-47 codes this voice handles well
    gender: str                  # "male" | "female" | "neutral"
    age_group: str               # "child" | "young" | "middle" | "elderly" | "unknown"
    tone: str                    # rough descriptor
    is_v3_verified: bool         # True if the voice is in verified_languages for current locale

    def to_profile(self):
        """Shape-compatible enough with engine.voice_pool.VoiceProfile for the pipeline."""
        return {
            "voice_id": self.voice_id,
            "name": self.name,
            "gender": self.gender,
            "age_group": self.age_group,
            "tone": self.tone,
            "best_for": ("narrator", "hero", "wise"),
            "priority": 1.3 if self.is_v3_verified else 1.0,
            "default_stability": 0.45,
            "default_similarity": 0.80,
            "default_style": 0.25,
        }


def _norm_lang(code: str) -> str:
    """Normalize a BCP-47 tag to its primary subtag (en-US → en, pt-BR stays)."""
    if not code:
        return ""
    code = code.strip().replace("_", "-")
    # Keep pt-BR distinct from pt; everything else collapses to primary.
    if code.lower() == "pt-br":
        return "pt-BR"
    return code.split("-")[0].lower()


def _classify_voice(v: dict) -> IntlVoice | None:
    labels = v.get("labels") or {}
    name = v.get("name", "")
    voice_id = v.get("voice_id") or v.get("voiceId") or ""
    if not voice_id:
        return None

    langs: set[str] = set()
    for entry in (v.get("verified_languages") or []):
        lc = _norm_lang(entry.get("language", ""))
        if lc:
            langs.add(lc)
    label_lang = _norm_lang(labels.get("language", ""))
    if label_lang:
        langs.add(label_lang)
    if not langs:
        # Untagged voices fall back to en (Eleven's de facto default for premade voices).
        langs.add("en")

    gender = (labels.get("gender") or "neutral").lower()
    if gender not in ("male", "female", "neutral"):
        gender = "neutral"

    age = (labels.get("age") or "").lower()
    age_map = {"young": "young", "middle aged": "middle", "middle-aged": "middle",
               "old": "elderly", "child": "child", "teen": "young"}
    age_group = age_map.get(age, "young")

    descriptive = (labels.get("descriptive") or labels.get("description") or "").lower()
    if "warm" in descriptive: tone = "warm"
    elif "deep" in descriptive: tone = "deep"
    elif "bright" in descriptive or "crisp" in descriptive: tone = "bright"
    elif "soft" in descriptive: tone = "soft"
    elif "raspy" in descriptive or "gruff" in descriptive: tone = "gruff"
    else: tone = "warm"

    return IntlVoice(
        voice_id=voice_id,
        name=name,
        languages=tuple(sorted(langs)),
        gender=gender,
        age_group=age_group,
        tone=tone,
        is_v3_verified=bool(v.get("verified_languages")),
    )


async def _fetch_shared_for_lang(session: aiohttp.ClientSession, api_key: str, lang: str) -> list[dict]:
    """Pull the top professional shared voices for a given language tag."""
    url = f"{ELEVENLABS_API}/v1/shared-voices"
    params = {"language": lang, "page_size": SHARED_PER_LANG, "category": "professional"}
    headers = {"xi-api-key": api_key}
    try:
        async with session.get(url, params=params, headers=headers,
                                timeout=aiohttp.ClientTimeout(total=30)) as r:
            if r.status != 200:
                logger.warning("shared-voices %s returned %s", lang, r.status)
                return []
            data = await r.json()
    except Exception as e:
        logger.warning("shared-voices %s failed: %s", lang, e)
        return []
    voices = data.get("voices") or data.get("shared_voices") or []
    # Each entry needs a "voice_id" — shared-voices use a different field sometimes
    out = []
    for v in voices:
        vid = v.get("voice_id") or v.get("voiceId") or v.get("public_owner_id")
        if vid:
            v["voice_id"] = vid
            out.append(v)
    return out


async def _fetch_voices_from_eleven() -> list[IntlVoice]:
    api_key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not api_key:
        logger.warning("ELEVENLABS_API_KEY not set — voice intl pool empty")
        return []
    headers = {"xi-api-key": api_key, "Accept": "application/json"}
    try:
        async with aiohttp.ClientSession() as s:
            # 1. workspace + premade voices
            async with s.get(f"{ELEVENLABS_API}/v1/voices", headers=headers,
                              timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status != 200:
                    logger.warning("Eleven /v1/voices returned %s", r.status)
                    return []
                data = await r.json()
            workspace_raw = data.get("voices", [])

            # 2. shared voices per supported language (in parallel)
            shared_batches = await asyncio.gather(*[
                _fetch_shared_for_lang(s, api_key, lang) for lang in SHARED_LANGS
            ])
    except Exception as e:
        logger.warning("Failed to fetch voices from ElevenLabs: %s", e)
        return []

    classified: list[IntlVoice] = []
    seen_ids: set[str] = set()
    for v in workspace_raw:
        c = _classify_voice(v)
        if c and c.voice_id not in seen_ids:
            classified.append(c)
            seen_ids.add(c.voice_id)
    shared_added = 0
    for batch in shared_batches:
        for v in batch:
            c = _classify_voice(v)
            if c and c.voice_id not in seen_ids:
                classified.append(c)
                seen_ids.add(c.voice_id)
                shared_added += 1
    logger.info("Voice pool: %d workspace + %d shared = %d total",
                len(workspace_raw), shared_added, len(classified))
    return classified


async def _get_all_voices() -> list[IntlVoice]:
    """Return cached voices; refresh from Eleven if stale."""
    now = time.time()
    if _cache["voices"] and (now - _cache["fetched_at"]) < CACHE_TTL_SEC:
        return _cache["voices"]
    async with _lock:
        # double-check after acquiring lock
        if _cache["voices"] and (now - _cache["fetched_at"]) < CACHE_TTL_SEC:
            return _cache["voices"]
        voices = await _fetch_voices_from_eleven()
        if voices:
            _cache["voices"] = voices
            _cache["fetched_at"] = now
    return _cache["voices"]


async def get_voices_for_locale(locale: str) -> list[IntlVoice]:
    """Return all voices that handle the given locale, sorted: v3-verified first,
    then by name for stability. Empty list if Eleven unreachable or no matches."""
    target = _norm_lang(locale)
    # Some locale-narrow targets (pt-BR) should also accept their primary tag (pt)
    # because ElevenLabs often labels just the language without region.
    accept = {target}
    if "-" in target:
        accept.add(target.split("-")[0].lower())
    all_voices = await _get_all_voices()
    matches = [v for v in all_voices if any(a in v.languages for a in accept)]
    matches.sort(key=lambda v: (not v.is_v3_verified, v.name))
    return matches


async def pick_voice_for_locale(
    locale: str,
    gender: str | None = None,
    age_group: str | None = None,
    role: str = "narrator",
) -> IntlVoice | None:
    """Light scoring: prefer curated-narrator for narrator role, then v3-verified
    voices, then gender/age match. Returns None when no voices available."""
    voices = await get_voices_for_locale(locale)
    if not voices:
        return None

    # For the narrator role, strongly prefer the curated voice (native + bedtime-tone).
    if role == "narrator" and locale in CURATED_NARRATORS:
        curated_id = CURATED_NARRATORS[locale]
        for v in voices:
            if v.voice_id == curated_id:
                return v
        # Curated voice not present in pool (rare), fall through to scoring.

    def score(v: IntlVoice) -> tuple:
        s = 0
        # Boost curated voice even outside narrator role (it's reliable)
        if v.voice_id == CURATED_NARRATORS.get(locale): s += 200
        if v.is_v3_verified: s += 100
        if gender and v.gender == gender: s += 30
        if age_group and v.age_group == age_group: s += 20
        if role == "narrator" and v.tone in ("warm", "soft"): s += 10
        if role == "hero" and v.tone in ("bright", "warm"): s += 8
        return (-s, v.name)

    voices.sort(key=score)
    return voices[0]


async def warm_cache():
    """Optional startup hook to pre-load the cache."""
    await _get_all_voices()
