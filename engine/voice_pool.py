# -*- coding: utf-8 -*-
"""20-voice pool for fairy tale generation. Each voice auditioned on Russian text."""

from dataclasses import dataclass


@dataclass(frozen=True)
class VoiceProfile:
    voice_id: str
    name: str
    gender: str          # "male" | "female"
    age_group: str       # "child" | "young" | "middle" | "elderly"
    tone: str            # "warm" | "deep" | "bright" | "raspy" | "soft" | "authoritative"
    best_for: tuple[str, ...]
    default_stability: float = 0.45
    default_similarity: float = 0.80
    default_style: float = 0.25


# ============================================================
# 20 auditioned voices — tested on Russian fairy tale text
# ============================================================
VOICE_POOL: list[VoiceProfile] = [
    # --- Female ---
    VoiceProfile("tHvkdDYIKRZSsD8v6JWR", "Irina",          "female", "young",   "soft",          ("narrator",)),
    VoiceProfile("GN4wbsbejSnGSa1AzjH5", "Ekaterina",      "female", "young",   "soft",          ("hero", "magical")),
    VoiceProfile("ymDCYd8puC7gYjxIamPt", "Marina_EL",      "female", "middle",  "warm",          ("narrator", "wise", "hero")),
    VoiceProfile("7NsaqHdLuKNFvEfjpUno", "Seer_Morganna",  "female", "elderly", "authoritative", ("wise", "narrator", "magical")),
    VoiceProfile("TPIitICAZ8CqlGZ81AKm", "Elen_Kuragina",  "female", "middle",  "deep",          ("villain", "magical")),
    VoiceProfile("rSfuQoQ3FY8SVKeraMAp", "Lunya",          "female", "young",   "soft",          ("magical", "narrator")),
    VoiceProfile("YjESejviApN7SHrbfnA2", "Nastya",         "female", "young",   "bright",        ("comic", "hero")),
    VoiceProfile("NhY0kyTmsKuEpHvDMngm", "Nataly",         "female", "young",   "soft",          ("magical", "hero")),
    VoiceProfile("xb0RCfp97gx711PCjTKw", "Kuki",           "female", "middle",  "soft",          ("magical", "wise", "narrator")),
    VoiceProfile("piI8Kku0DcvcL6TTSeQt", "Flicker",        "female", "young",   "bright",        ("hero", "comic", "magical")),

    # --- Male ---
    VoiceProfile("lxY8Pn0yWs1Ve9rBceah", "Ivan_Nazarov",   "male",   "middle",  "warm",          ("narrator",)),
    VoiceProfile("WTn2eCRCpoFAC50VD351", "Artem",          "male",   "young",   "bright",        ("hero", "comic")),
    VoiceProfile("ogi2DyUAKJb7CEdqqvlU", "Stanislav",      "male",   "middle",  "deep",          ("narrator", "wise", "hero")),
    VoiceProfile("iBRcUZbbi4hxPMzDCm71", "GrandPa_Danish", "male",   "elderly", "raspy",         ("wise", "comic")),
    VoiceProfile("pM78bgjPVk0JXtaEnFoj", "Nester_Surovy",  "male",   "middle",  "deep",          ("villain",)),
    VoiceProfile("RLRdvNFwJJct2XZOgfzy", "Mishka_Yaponcik","male",   "middle",  "bright",        ("comic", "hero")),
    VoiceProfile("cPoqAvGWCPfCfyPMwe4z", "Victor",         "male",   "elderly", "deep",          ("villain", "hero")),
    VoiceProfile("hD8aK7CmEPgH3mbFO08e", "Ivo",            "male",   "young",   "soft",          ("hero", "magical")),
    VoiceProfile("OwKgYRjZnJnXyWDEgF1J", "DemiMark",       "male",   "middle",  "authoritative", ("wise", "narrator", "villain")),
    VoiceProfile("ZEchI3lWet1JsdNubYRY", "Darth",          "male",   "young",   "bright",        ("hero", "comic")),
]

# Scoring tables
_AGE_SCORE = {
    ("child", "child"): 1.0, ("child", "young"): 0.8, ("child", "middle"): 0.2, ("child", "elderly"): 0.0,
    ("young", "young"): 1.0, ("young", "child"): 0.7, ("young", "middle"): 0.6,
    ("middle", "middle"): 1.0, ("middle", "young"): 0.5,
    ("elderly", "elderly"): 1.0, ("elderly", "middle"): 0.5,
}

_ROLE_TONE_SCORE = {
    ("narrator", "warm"): 1.0, ("narrator", "soft"): 0.8, ("narrator", "authoritative"): 0.7,
    ("hero", "bright"): 0.9, ("hero", "warm"): 0.8, ("hero", "soft"): 0.7,
    ("villain", "deep"): 1.0, ("villain", "raspy"): 0.9, ("villain", "authoritative"): 0.7,
    ("wise", "authoritative"): 0.9, ("wise", "raspy"): 0.8, ("wise", "soft"): 0.7, ("wise", "warm"): 0.7,
    ("comic", "bright"): 1.0, ("comic", "raspy"): 0.7,
    ("magical", "soft"): 1.0, ("magical", "warm"): 0.8, ("magical", "bright"): 0.6,
    ("animal", "warm"): 1.0, ("animal", "bright"): 0.9, ("animal", "raspy"): 0.8, ("animal", "soft"): 0.7,
}


def pick_voice(
    gender: str,
    age: str,
    role: str,
    already_used: dict[str, str] | None = None,
) -> VoiceProfile:
    """Pick the best voice for given character traits, avoiding duplicates."""
    used_ids = set((already_used or {}).values())

    scored: list[tuple[float, VoiceProfile]] = []
    for v in VOICE_POOL:
        if v.gender != gender:
            continue

        age_s = _AGE_SCORE.get((age, v.age_group), 0.3)
        tone_s = _ROLE_TONE_SCORE.get((role, v.tone), 0.3)
        role_bonus = 0.3 if role in v.best_for else 0.0
        score = age_s * 0.3 + tone_s * 0.5 + role_bonus * 0.2

        # Children should never get deep/authoritative voices
        if age == "child" and v.tone in ("deep", "authoritative"):
            score *= 0.2

        # Prefer bright/soft tones for children
        if age == "child" and v.tone in ("bright", "soft"):
            score *= 1.3

        if v.voice_id in used_ids:
            score *= 0.5

        scored.append((score, v))

    scored.sort(key=lambda x: x[0], reverse=True)

    if not scored:
        for v in VOICE_POOL:
            if v.gender == gender:
                return v
        return VOICE_POOL[0]

    return scored[0][1]
