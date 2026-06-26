"""Spoken brand intro prepended to every generated story.

Three templates rotate randomly so listeners don't hear the same opening each
time their child gets a new story. All voiced by the narrator. Title is
substituted via `{title}`. ElevenLabs v3 audio tags drive the cadence.
"""
import random
from typing import Iterable

_TEMPLATES_RU: list[str] = [
    # v3 — Прямое тёплое обращение к ребёнку
    "[soft] [slows down] Слушай, маленький... [pause] эта сказка только для тебя. "
    "[long pause] [whispers] {title}.",
    # v4 — Мини-сюжет «в библиотеке Сказика»
    "[slows down] [mysterious] В волшебной библиотеке Ска-а-азика... [pause] "
    "нашлась новая история. [long pause] {title}.",
    # v5 — Колыбельный шёпот, bedtime-mood
    "[whispers] [slows down] Тс-с-с... [pause] [soft] начинается сказка. "
    "[long pause] [whispers] {title}.",
]


def pick_intro_text(title: str, locale: str = "ru", rng: random.Random | None = None) -> str | None:
    """Return a randomly-chosen intro line with the title substituted.
    Returns None when we don't have templates for the locale (we currently
    only ship Russian templates — Lalaka non-RU locales skip the intro).
    """
    if (locale or "ru") != "ru":
        return None
    pool: Iterable[str] = _TEMPLATES_RU
    chooser = rng or random
    return chooser.choice(list(pool)).format(title=(title or "").strip())
