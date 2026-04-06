# -*- coding: utf-8 -*-
"""~58-voice pool for fairy tale generation.

Each voice auditioned on Russian text. Pool covers:
- Russian-native voices for narrators, heroes, villains, wise characters
- Character/animation voices for animals, magical creatures, children
- Diverse age groups: child, young, middle, elderly
- Diverse tones: warm, deep, bright, raspy, soft, authoritative, squeaky, gruff
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VoiceProfile:
    voice_id: str
    name: str
    gender: str          # "male" | "female" | "neutral"
    age_group: str       # "child" | "young" | "middle" | "elderly"
    tone: str            # "warm" | "deep" | "bright" | "raspy" | "soft" | "authoritative" | "squeaky" | "gruff"
    best_for: tuple[str, ...]
    priority: float = 1.0   # 1.0 = normal, 1.3 = auditioned & confirmed best-in-class
    default_stability: float = 0.45
    default_similarity: float = 0.80
    default_style: float = 0.25


# ============================================================
# ~58 auditioned voices — tested on Russian fairy tale text
# ★ = priority voices (auditioned and confirmed best-in-class)
# ============================================================
VOICE_POOL: list[VoiceProfile] = [
    # ==========================================
    #   RUSSIAN-NATIVE FEMALE VOICES (16)
    # ==========================================

    # --- Narrators ---
    VoiceProfile("tHvkdDYIKRZSsD8v6JWR", "Irina",            "female", "young",    "soft",          ("narrator",)),
    VoiceProfile("ymDCYd8puC7gYjxIamPt", "Marina_EL",         "female", "middle",   "warm",          ("narrator", "wise", "hero")),
    VoiceProfile("FnNYfPjyvZwLwb043Kl1", "Paula",             "female", "middle",   "warm",          ("narrator", "wise")),
    VoiceProfile("WfExDXCt2GBg6MI5KjQk", "Kate_Stories",      "female", "middle",   "warm",          ("narrator", "hero")),
    VoiceProfile("8oyYKsAD3g8uVOXOQB4Z", "Elena",             "female", "middle",   "authoritative", ("narrator", "wise")),
    VoiceProfile("FZGeNF7bE3syeQOynDKC", "Victoria",          "female", "middle",   "warm",          ("narrator", "wise")),

    # --- Heroines & magical ---
    VoiceProfile("GN4wbsbejSnGSa1AzjH5", "Ekaterina",         "female", "young",    "soft",          ("hero", "magical")),
    VoiceProfile("rSfuQoQ3FY8SVKeraMAp", "Lunya",             "female", "young",    "soft",          ("magical", "narrator")),
    VoiceProfile("NhY0kyTmsKuEpHvDMngm", "Nataly",            "female", "young",    "soft",          ("magical", "hero")),
    VoiceProfile("rxEz5E7hIAPk7D3bXwf6", "Anna",              "female", "young",    "soft",          ("narrator", "magical", "hero")),
    VoiceProfile("C3FusDjPequ6qFchqpzu", "Ekaterina2",        "female", "young",    "warm",          ("hero", "narrator")),
    VoiceProfile("Jbte7ht1CqapnZvc4KpK", "Kari",              "female", "young",    "warm",          ("hero", "narrator")),
    VoiceProfile("AB9XsbSA4eLG12t2myjN", "Larisa",            "female", "young",    "bright",        ("hero", "narrator")),
    VoiceProfile("ycbyWsnf4hqZgdpKHqiU", "Rina",              "female", "young",    "soft",          ("narrator", "magical")),

    # --- Bright & comic ---
    VoiceProfile("YjESejviApN7SHrbfnA2", "Nastya",            "female", "young",    "bright",        ("comic", "hero")),
    VoiceProfile("piI8Kku0DcvcL6TTSeQt", "Flicker",           "female", "young",    "bright",        ("hero", "comic", "magical")),
    VoiceProfile("bi0tSQTrp58MDdPUkrEl", "Klava",             "female", "middle",   "bright",        ("comic", "hero", "animal")),

    # --- Deep female ---
    VoiceProfile("TPIitICAZ8CqlGZ81AKm", "Elen_Kuragina",     "female", "middle",   "deep",          ("narrator", "magical")),
    VoiceProfile("OowtKaZH9N7iuGbsd00l", "Veronica",          "female", "middle",   "deep",          ("narrator", "hero")),

    # --- Wise elderly ---
    VoiceProfile("7NsaqHdLuKNFvEfjpUno", "Seer_Morganna",     "female", "elderly",  "authoritative", ("wise", "narrator", "magical")),

    # ==========================================
    #   RUSSIAN-NATIVE MALE VOICES (18)
    # ==========================================

    # --- Narrators ---
    VoiceProfile("lxY8Pn0yWs1Ve9rBceah", "Ivan_Nazarov",      "male",   "middle",   "warm",          ("narrator",)),
    VoiceProfile("pvY1pikBdoI4SB62vEVo", "Andrei",            "male",   "middle",   "warm",          ("narrator", "hero")),
    VoiceProfile("C1Jbh3J5Tp1r1TyKaVSY", "Egor",             "male",   "middle",   "soft",          ("narrator", "wise")),
    VoiceProfile("vQxSi2EuaRWwBw3nn6dK", "Marat",             "male",   "middle",   "warm",          ("narrator", "hero")),
    VoiceProfile("XuEV9VY3VUASYgJVNBh0", "Sergey",            "male",   "middle",   "deep",          ("narrator", "wise")),
    VoiceProfile("HcaxAsrhw4ByUo4CBCBN", "Maxim",             "male",   "middle",   "soft",          ("narrator",)),
    VoiceProfile("TUQNWEvVPBLzMBSVDPUA", "Alex_Bell",         "male",   "middle",   "deep",          ("narrator", "wise")),
    VoiceProfile("MYw0upsxdtxs1n97djly", "Georgy",            "male",   "middle",   "warm",          ("narrator",)),

    # --- Heroes & young ---
    VoiceProfile("WTn2eCRCpoFAC50VD351", "Artem",             "male",   "young",    "bright",        ("hero", "comic")),
    VoiceProfile("ZEchI3lWet1JsdNubYRY", "Darth",             "male",   "young",    "bright",        ("hero", "comic", "narrator"), 1.3),
    VoiceProfile("zvm1P65eFt40xSwMli2k", "Garry",             "male",   "young",    "warm",          ("hero", "animal")),
    VoiceProfile("O88Glmkh2nWihrGwNsFd", "Roman",             "male",   "middle",   "warm",          ("hero", "narrator")),
    VoiceProfile("gJEfHTTiifXEDmO687lC", "Prince_Nur",        "male",   "young",    "deep",          ("hero", "narrator")),
    VoiceProfile("6A9D8WSMm4rFsg2DWFeE", "Egor_Gadzhiyev",    "male",   "middle",   "authoritative", ("hero", "narrator")),

    # --- Villains & dark ---
    VoiceProfile("pM78bgjPVk0JXtaEnFoj", "Nester_Surovy",     "male",   "middle",   "deep",          ("villain",), 1.3),
    VoiceProfile("OwKgYRjZnJnXyWDEgF1J", "DemiMark",          "male",   "middle",   "authoritative", ("wise", "narrator")),
    VoiceProfile("GquPFn9xormgmHaJ2KdN", "Geremy",            "male",   "middle",   "deep",          ("villain", "magical")),
    VoiceProfile("2gPFXx8pN3Avh27Dw5Ma", "Oxley_Evil",        "male",   "middle",   "deep",          ("villain",)),

    # --- Comic & energetic ---
    VoiceProfile("txnCCHHGKmYIwrn7HfHQ", "Alexandr_Vlasov",   "male",   "middle",   "bright",        ("comic", "narrator")),
    VoiceProfile("hU3rD0Yk7DoiYULTX1pD", "Dmitry_D",          "male",   "middle",   "bright",        ("comic", "narrator")),

    # --- Wise elderly ---
    VoiceProfile("iBRcUZbbi4hxPMzDCm71", "GrandPa_Danish",    "male",   "elderly",  "raspy",         ("wise", "comic", "narrator"), 1.3),
    VoiceProfile("cPoqAvGWCPfCfyPMwe4z", "Victor",            "male",   "elderly",  "deep",          ("villain", "hero"), 1.3),

    # ==========================================
    #   CHARACTER / ANIMATION VOICES (10)
    #   (non-Russian native, great for specific roles
    #    — ElevenLabs v3 renders them well in Russian)
    # ==========================================

    # --- Child / small animal voices (★ = priority) ---
    VoiceProfile("M5t0724ORuAGCh3p3DUR", "Miffy_Mouse",       "neutral", "child",   "squeaky",       ("animal", "comic", "hero")),
    VoiceProfile("VD1if7jDVYtAKs4P0FIY", "Milly_Maple",       "female",  "child",   "bright",        ("hero", "comic", "animal")),
    VoiceProfile("XJ2fW4ybq7HouelYYGcL", "Cherry_Twinkle",    "female",  "child",   "bright",        ("hero", "comic", "animal", "magical"), 1.3),
    VoiceProfile("ocZQ262SsZb9RIxcQBOj", "Lulu_Lollipop",     "female",  "child",   "squeaky",       ("animal", "comic", "hero"), 1.3),
    VoiceProfile("nDJIICjR9zfJExIFeSCN", "Emmaline",          "female",  "child",   "soft",          ("hero", "narrator", "magical")),

    # --- Large animal / gruff voices ---
    VoiceProfile("2OcnG4mH3jIMtWz3vKus", "Wolf_Spencer",      "male",   "elderly",  "gruff",         ("villain", "animal")),
    VoiceProfile("8TMmdpPgqHKvDOGYP2lN", "Gregory_Bear",      "male",   "elderly",  "gruff",         ("animal", "wise", "comic")),

    # --- Magical / wise character voices (★ = priority) ---
    VoiceProfile("NOpBlnGInO9m6vDvFkFC", "Spuds_Grandpa",     "male",   "elderly",  "warm",          ("wise", "narrator", "comic"), 1.3),
    VoiceProfile("xsSg7GkDPDhaGZpbKOLn", "Tomasz_Fairyland",  "male",   "elderly",  "deep",          ("narrator", "magical", "wise")),
    VoiceProfile("6sFKzaJr574YWVu4UuJF", "Cornelius_Wizard",  "male",   "elderly",  "raspy",         ("wise", "magical", "narrator")),
    VoiceProfile("1wg2wOjdEWKA7yQD8Kca", "Father_Christmas",  "male",   "elderly",  "warm",          ("wise", "narrator", "magical")),
    VoiceProfile("oae6GCCzwoEbfc5FHdEu", "William_Bedtime",   "male",   "elderly",  "soft",          ("narrator", "wise")),
    VoiceProfile("ouL9IsyrSnUkCmfnD02u", "Grimblewood",       "male",   "elderly",  "raspy",         ("comic", "magical", "animal")),

    # --- Female conversational & spirited ---
    VoiceProfile("jqcCZkN6Knx8BJ5TBdYR", "Zara",              "female", "young",    "warm",          ("hero", "narrator")),
    VoiceProfile("i4CzbCVWoqvD0P1QJCUL", "Ivy",               "female", "young",    "bright",        ("hero", "comic", "magical")),

    # --- Female villain ---
    VoiceProfile("esy0r39YPLQjOczyOib8", "Britney_Villain",   "female", "middle",   "deep",          ("villain", "magical")),

    # --- Male elderly / deep character ---
    VoiceProfile("7p1Ofvcwsv7UBPoFNcpI", "Julian",            "male",   "elderly",  "deep",          ("narrator", "wise")),
    VoiceProfile("qAZH0aMXY8tw1QufPN0D", "Flint",             "male",   "elderly",  "raspy",         ("narrator", "wise", "villain"), 1.3),
    VoiceProfile("4YYIPFl9wE5c4L2eu2Gb", "Burt_Reynolds",     "male",   "middle",   "deep",          ("narrator", "hero")),

    # --- Cartoon / comic voices ---
    VoiceProfile("DUnzBkwtjRWXPr6wRbmL", "Mad_Scientist",     "male",   "young",    "bright",        ("comic", "villain", "magical")),
    VoiceProfile("WOY6pnQ1WCg0mrOZ54lM", "Thorthugo",         "male",   "young",    "bright",        ("animal", "comic")),
]

# Scoring tables
_AGE_SCORE = {
    ("child", "child"): 1.0, ("child", "young"): 0.8, ("child", "middle"): 0.2, ("child", "elderly"): 0.0,
    ("young", "young"): 1.0, ("young", "child"): 0.7, ("young", "middle"): 0.6,
    ("middle", "middle"): 1.0, ("middle", "young"): 0.5,
    ("elderly", "elderly"): 1.0, ("elderly", "middle"): 0.5,
}

_ROLE_TONE_SCORE = {
    ("narrator", "warm"): 1.0, ("narrator", "soft"): 0.8, ("narrator", "authoritative"): 0.7, ("narrator", "deep"): 0.6,
    ("hero", "bright"): 0.9, ("hero", "warm"): 0.8, ("hero", "soft"): 0.7,
    ("villain", "deep"): 1.0, ("villain", "raspy"): 0.9, ("villain", "authoritative"): 0.7, ("villain", "gruff"): 0.8,
    ("wise", "authoritative"): 0.9, ("wise", "raspy"): 0.8, ("wise", "soft"): 0.7, ("wise", "warm"): 0.7, ("wise", "deep"): 0.6,
    ("comic", "bright"): 1.0, ("comic", "raspy"): 0.7, ("comic", "squeaky"): 0.9, ("comic", "gruff"): 0.6,
    ("magical", "soft"): 1.0, ("magical", "warm"): 0.8, ("magical", "bright"): 0.6, ("magical", "raspy"): 0.5,
    ("animal", "warm"): 1.0, ("animal", "bright"): 0.9, ("animal", "raspy"): 0.8, ("animal", "soft"): 0.7,
    ("animal", "squeaky"): 1.0, ("animal", "gruff"): 0.9,
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
        # Gender filter: "neutral" voices can match any gender request.
        # For children: allow female/neutral child voices for male characters too
        # (standard practice in animation — women voice boys).
        if age == "child":
            if v.age_group != "child" and v.gender != gender and v.gender != "neutral":
                continue
        else:
            if v.gender != gender and v.gender != "neutral":
                continue

        age_s = _AGE_SCORE.get((age, v.age_group), 0.3)
        tone_s = _ROLE_TONE_SCORE.get((role, v.tone), 0.3)
        role_bonus = 0.3 if role in v.best_for else 0.0
        score = (age_s * 0.3 + tone_s * 0.5 + role_bonus * 0.2) * v.priority

        # Children should never get deep/authoritative voices
        if age == "child" and v.tone in ("deep", "authoritative"):
            score *= 0.2

        # Prefer bright/soft/squeaky tones for children
        if age == "child" and v.tone in ("bright", "soft", "squeaky"):
            score *= 1.3

        # Animals prefer character voices with matching tones
        if role == "animal" and v.tone in ("squeaky", "gruff", "raspy"):
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
