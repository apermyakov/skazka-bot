# -*- coding: utf-8 -*-
"""Async LLM client via OpenRouter (Gemini 2.5 Pro)."""

import json
import logging
import re
import time

import aiohttp

from bot.config import settings
from engine.story_parser import SCREENWRITER_PROMPT
from db.database import log_api_call, fire

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


# Locale → English name of the language, used in meta-instructions to the LLM.
# When passed, the LLM is told to write the output in that language. The TITLE
# marker stays English so parsing is uniform across locales.
_LOCALE_LANG_NAME = {
    "en": "English",
    "de": "German",
    "es": "Spanish",
    "fr": "French",
    "it": "Italian",
    "pl": "Polish",
    "pt-BR": "Brazilian Portuguese",
    "tr": "Turkish",
    "ja": "Japanese",
    "ko": "Korean",
    "ar": "Modern Standard Arabic",
    "ru": "Russian",
    "uk": "Ukrainian",
}


# Per-locale story prompts. Written IN the target language so the model's
# strongest signal points the same direction as the locale. Each value:
#   system   — the LLM system prompt (role/persona, language lock)
#   write    — short imperative that opens the user message ("Write a fairy
#              tale" in that language) before the user's topic
#   reminder — last line of the user message, repeats the language lock
_LOCALE_STORY_PROMPTS: dict[str, dict] = {
    "en":   {"system":   "You are a talented children's writer. Write exclusively in English. Never switch to another language.",
             "write":    "Write a bedtime fairy tale for a young child based on the prompt below.",
             "reminder": "REMINDER: the entire output — title and body — must be in English. First line MUST be: TITLE: <story name in English>"},
    "de":   {"system":   "Du bist eine talentierte Kinderbuchautorin. Schreibe ausschließlich auf Deutsch. Wechsle niemals in eine andere Sprache.",
             "write":    "Schreibe eine Gutenacht-Geschichte für ein kleines Kind, basierend auf dem folgenden Auftrag.",
             "reminder": "ERINNERUNG: die gesamte Ausgabe – Titel und Text – muss auf Deutsch sein. Erste Zeile MUSS sein: TITLE: <Titel auf Deutsch>"},
    "es":   {"system":   "Eres un talentoso autor de cuentos para niños. Escribe exclusivamente en español. Nunca cambies a otro idioma.",
             "write":    "Escribe un cuento para dormir para un niño pequeño basado en la siguiente petición.",
             "reminder": "RECORDATORIO: toda la salida —título y cuerpo— debe estar en español. La primera línea DEBE ser: TITLE: <título del cuento en español>"},
    "fr":   {"system":   "Tu es un talentueux auteur de contes pour enfants. Écris exclusivement en français. Ne change jamais de langue.",
             "write":    "Écris un conte du soir pour un jeune enfant à partir de la demande ci-dessous.",
             "reminder": "RAPPEL : toute la sortie — titre et corps — doit être en français. La première ligne DOIT être : TITLE: <titre du conte en français>"},
    "it":   {"system":   "Sei un talentuoso autore di favole per bambini. Scrivi esclusivamente in italiano. Non cambiare mai lingua.",
             "write":    "Scrivi una favola della buonanotte per un bambino piccolo basata sulla richiesta qui sotto.",
             "reminder": "PROMEMORIA: l'intero output — titolo e corpo — deve essere in italiano. La prima riga DEVE essere: TITLE: <titolo della favola in italiano>"},
    "pl":   {"system":   "Jesteś utalentowanym autorem bajek dla dzieci. Pisz wyłącznie po polsku. Nigdy nie zmieniaj języka.",
             "write":    "Napisz bajkę na dobranoc dla małego dziecka na podstawie poniższego polecenia.",
             "reminder": "PRZYPOMNIENIE: cały tekst — tytuł i treść — musi być po polsku. Pierwszy wiersz MUSI brzmieć: TITLE: <tytuł bajki po polsku>"},
    "pt-BR":{"system":   "Você é um talentoso autor de histórias infantis. Escreva exclusivamente em português brasileiro. Nunca mude de idioma.",
             "write":    "Escreva uma história de ninar para uma criança pequena com base no pedido abaixo.",
             "reminder": "LEMBRETE: toda a saída — título e corpo — deve estar em português brasileiro. A primeira linha DEVE ser: TITLE: <título em português>"},
    "tr":   {"system":   "Sen yetenekli bir çocuk masalı yazarısın. Yalnızca Türkçe yaz. Asla başka bir dile geçme.",
             "write":    "Aşağıdaki istek üzerine küçük bir çocuk için bir uyku masalı yaz.",
             "reminder": "HATIRLATMA: tüm çıktı — başlık ve gövde — Türkçe olmalı. İlk satır şu OLMALIDIR: TITLE: <Türkçe masal başlığı>"},
    "ja":   {"system":   "あなたは才能ある児童文学作家です。日本語のみで書いてください。他の言語に切り替えないでください。",
             "write":    "下記のリクエストに基づいて、小さなお子様向けの就寝前の童話を書いてください。",
             "reminder": "リマインダー: 出力全体 — タイトルと本文 — はすべて日本語でなければなりません。最初の行は必ず次のようにしてください: TITLE: <日本語のタイトル>"},
    "ko":   {"system":   "당신은 재능 있는 아동 동화 작가입니다. 한국어로만 글을 써주세요. 절대 다른 언어로 바꾸지 마세요.",
             "write":    "아래 요청을 바탕으로 어린 아이를 위한 잠자리 동화를 써주세요.",
             "reminder": "알림: 출력물 전체 — 제목과 본문 — 은 모두 한국어여야 합니다. 첫 번째 줄은 반드시 다음과 같아야 합니다: TITLE: <한국어 동화 제목>"},
    "ar":   {"system":   "أنت مؤلف موهوب لأدب الأطفال. اكتب باللغة العربية فقط. لا تتحول إلى أي لغة أخرى أبدا.",
             "write":    "اكتب قصة ما قبل النوم لطفل صغير بناء على الطلب أدناه.",
             "reminder": "تذكير: يجب أن يكون الناتج بأكمله — العنوان والنص — باللغة العربية. يجب أن يكون السطر الأول: TITLE: <عنوان القصة بالعربية>"},
}


def _i18n_prefix(locale: str | None) -> str:
    """Meta-instruction prepended to the user prompt for non-Russian locales.
    Empty for None/ru so skazik's prompts run unchanged."""
    if not locale or locale == "ru":
        return ""
    lang = _LOCALE_LANG_NAME.get(locale, "English")
    return (
        f"IMPORTANT META-INSTRUCTION: Write the entire story and title in {lang}. "
        f"The first line MUST be exactly: TITLE: <name of the story in {lang}>\n"
        f"Use only {lang}. Do not include English or Russian translations.\n\n"
    )


def _extract_title(response: str, fallback: str) -> tuple[str, str]:
    """Extract title and body from an LLM story response. Handles both Russian
    'ЗАГОЛОВОК:' marker (skazik) and English 'TITLE:' marker (lalaka i18n)."""
    lines = response.strip().split("\n")
    title = ""
    text = response.strip()
    for i, line in enumerate(lines):
        s = line.strip()
        upper = s.upper()
        if upper.startswith("ЗАГОЛОВОК:"):
            title = s[len("ЗАГОЛОВОК:"):].strip().strip('"').strip("«»").strip("「」")
            text = "\n".join(lines[i+1:]).strip()
            break
        if upper.startswith("TITLE:"):
            title = s[len("TITLE:"):].strip().strip('"').strip("«»").strip("「」")
            text = "\n".join(lines[i+1:]).strip()
            break
        if i == 0 and len(s) < 100 and not s.endswith(".") and not s.endswith("。"):
            # First short line without period = likely title
            title = s.strip('"').strip("«»").strip("「」")
            text = "\n".join(lines[i+1:]).strip()
            break
    if not title:
        title = fallback
    return title[:200], text


async def _call_llm(system: str, user: str, max_retries: int = 3,
                    story_id: int = None, purpose: str = "llm",
                    temperature: float = None, max_tokens: int = None) -> str:
    """Call OpenRouter API and return the assistant's text response."""
    from db.config_manager import cfg
    model = await cfg.get("model.llm", settings.llm_model)
    temp = temperature if temperature is not None else await cfg.get("llm.screenplay_temperature", 0.8)
    tokens = max_tokens if max_tokens is not None else await cfg.get("llm.screenplay_max_tokens", 8000)

    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temp,
        "max_tokens": tokens,
    }

    from engine.http_session import get_session
    fallback_model = await cfg.get("model.llm_fallback", "google/gemini-3.5-flash")
    fallback_reasoning = await cfg.get("model.llm_fallback_reasoning", "high")
    per_call_timeout = float(await cfg.get("llm.call_timeout_sec", 90))

    for attempt in range(1, max_retries + 1):
        # Primary model on the first attempt; if it stalls or errors, retries use
        # a fast stable fallback so generation never hangs on a slow/preview model.
        if attempt == 1:
            payload["model"] = model
            payload.pop("reasoning", None)
        else:
            payload["model"] = fallback_model
            # Flash 3.5 (vanilla) is a thinking model that returns empty content ~40%
            # of the time unless reasoning effort is pinned. high = 5/5 reliability.
            if fallback_reasoning:
                payload["reasoning"] = {"effort": fallback_reasoning}
        t0 = time.time()
        try:
            session = get_session()
            async with session.post(OPENROUTER_URL, json=payload, headers=headers,
                                    timeout=aiohttp.ClientTimeout(total=per_call_timeout)) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        duration_ms = int((time.time() - t0) * 1000)
                        logger.warning("LLM HTTP %d (attempt %d): %s", resp.status, attempt, body[:300])
                        fire(log_api_call(story_id=story_id, service="openrouter", model=payload["model"],
                                          purpose=purpose, status="failed", duration_ms=duration_ms,
                                          request_text=user[:10000], error=body[:1000]))
                        continue
                    data = await resp.json()
                    duration_ms = int((time.time() - t0) * 1000)
                    content = data["choices"][0]["message"]["content"]
                    usage = data.get("usage", {})
                    if not content or not content.strip():
                        logger.warning("LLM returned empty content (attempt %d)", attempt)
                        continue
                    fire(log_api_call(story_id=story_id, service="openrouter", model=payload["model"],
                                      purpose=purpose, status="success", duration_ms=duration_ms,
                                      request_text=user[:10000], response_text=content[:10000],
                                      tokens_in=usage.get("prompt_tokens"),
                                      tokens_out=usage.get("completion_tokens")))
                    return content
        except Exception as e:
            duration_ms = int((time.time() - t0) * 1000)
            # asyncio.TimeoutError carries no message — str(e) == "". Make it self-documenting.
            err_msg = str(e) or f"{type(e).__name__} after {duration_ms}ms (timeout={per_call_timeout}s)"
            logger.warning("LLM error (attempt %d): %s", attempt, err_msg)
            fire(log_api_call(story_id=story_id, service="openrouter", model=payload["model"],
                              purpose=purpose, status="failed", duration_ms=duration_ms,
                              request_text=user[:10000], error=err_msg[:1000]))

    raise RuntimeError("LLM failed after all retries")


def _extract_json(text: str) -> dict:
    """Extract JSON from LLM response, stripping markdown fences."""
    cleaned = re.sub(r"```(?:json)?\s*", "", text)
    cleaned = cleaned.strip().rstrip("`")

    # Try to find JSON object
    start = cleaned.find("{")
    if start == -1:
        raise ValueError("No JSON object found in response")

    depth = 0
    end = start
    for i in range(start, len(cleaned)):
        if cleaned[i] == "{":
            depth += 1
        elif cleaned[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    return json.loads(cleaned[start:end])


async def generate_screenplay(context: str, story_id: int = None) -> dict:
    """Generate a structured fairy tale screenplay.

    Args:
        context: Topic and child info from user.

    Returns:
        Dict with keys: title, characters, segments, scenes.
    """
    from db.config_manager import cfg
    screenwriter_prompt = await cfg.get("prompt.screenwriter", SCREENWRITER_PROMPT)
    system_prompt = await cfg.get("prompt.screenwriter_system",
                                   "Ты генерируешь ТОЛЬКО валидный JSON. Никакого текста до или после JSON.")
    prompt = screenwriter_prompt.format(context=context)

    for attempt in range(1, 4):
        response = await _call_llm(
            system=system_prompt,
            user=prompt,
            story_id=story_id,
            purpose="screenplay",
        )
        logger.info("Screenplay LLM response (attempt %d): length=%d, start=%s",
                     attempt, len(response) if response else 0,
                     (response[:100] if response else "EMPTY"))

        if not response or not response.strip():
            logger.warning("Empty screenplay response (attempt %d)", attempt)
            continue

        try:
            screenplay = _extract_json(response)
            break
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("Screenplay JSON parse failed (attempt %d): %s", attempt, e)
            if attempt == 3:
                raise

    # Validate required fields
    required = {"title", "characters", "segments"}
    missing = required - set(screenplay.keys())
    if missing:
        raise ValueError(f"Screenplay missing fields: {missing}")

    # Ensure narrator exists
    char_ids = {c["id"] for c in screenplay["characters"]}
    if "narrator" not in char_ids:
        raise ValueError("Screenplay must have a 'narrator' character")

    # Validate segments
    import re as _re
    new_segs = []
    for seg in screenplay["segments"]:
        if seg["character_id"] not in char_ids:
            raise ValueError(f"Segment references unknown character: {seg['character_id']}")
        text = seg.get("text", "")
        if len(text) <= 250:
            new_segs.append(seg)
        else:
            sentences = _re.split(r'(?<=[.!?])\s+', text)
            current = ""
            for s in sentences:
                if len(current) + len(s) + 1 > 250 and current:
                    new_seg = dict(seg)
                    new_seg["text"] = current.strip()
                    new_segs.append(new_seg)
                    current = s
                else:
                    current = (current + " " + s).strip() if current else s
            if current.strip():
                new_seg = dict(seg)
                new_seg["text"] = current.strip()
                new_segs.append(new_seg)
    screenplay["segments"] = new_segs

    logger.info(
        "Screenplay generated: '%s', %d characters, %d segments",
        screenplay["title"],
        len(screenplay["characters"]),
        len(screenplay["segments"]),
    )
    return screenplay


async def generate_story_text(context: str, story_id: int = None, locale: str | None = None) -> dict:
    """Generate plain text fairy tale (no JSON, no audio tags).

    When `locale` is None or 'ru', behaves exactly as before (skazik path).
    When set to another locale, the LLM is instructed to write in that language.

    Returns:
        Dict with keys: title, text.
    """
    from db.config_manager import cfg
    prompt_template = await cfg.get("prompt.story_text", "Напиши сказку.\n{context}")
    system = await cfg.get("prompt.story_text_system", "Ты — талантливый детский писатель.")
    # For non-Russian locales we write the system prompt + user template in the
    # TARGET language itself. Models anchor far more reliably to the prompt
    # language than to a meta-instruction translated into English — a Turkish
    # system prompt produces a Turkish story; an English meta-prefix in front
    # of a Russian template produces ~50% Russian stories.
    loc_prompts = _LOCALE_STORY_PROMPTS.get(locale or "ru")
    if loc_prompts:
        system = loc_prompts["system"]
        prompt = f"{loc_prompts['write']}\n{context}\n\n{loc_prompts['reminder']}"
    else:
        prompt = _i18n_prefix(locale) + prompt_template.format(context=context)

    response = await _call_llm(
        system=system,
        user=prompt,
        story_id=story_id,
        purpose="story_text",
    )

    if not response or not response.strip():
        raise RuntimeError("Empty story text response")

    fallback = "Bedtime story" if (locale and locale != "ru") else "Сказка на ночь"
    title, text = _extract_title(response, fallback)
    if len(text) > 15000:
        text = text[:15000]
        logger.warning("Story text truncated from %d to 15000 chars", len(response))

    logger.info("Story text generated [%s]: '%s', %d chars", locale or "ru", title, len(text))
    return {"title": title, "text": text}


def _detect_story_locale(text: str, fallback: str | None) -> str | None:
    """Pick the locale whose script dominates the existing story text.

    The order's stored locale is what the user *visited* (e.g. /en/create) —
    that's not always the language the story was actually written in (Russian
    topic on the EN page produces a Russian story half the time). On revise we
    want to preserve the language the user is reading, not silently translate.
    """
    if not text:
        return fallback
    counts = {"ja": 0, "ko": 0, "ar": 0, "ru": 0, "en": 0}
    for ch in text:
        o = ord(ch)
        if 0x0400 <= o <= 0x04FF:   counts["ru"] += 1   # Cyrillic
        elif 0x3040 <= o <= 0x30FF: counts["ja"] += 1   # Hiragana + Katakana
        elif 0x4E00 <= o <= 0x9FFF: counts["ja"] += 1   # CJK Unified Ideographs (Kanji)
        elif 0xAC00 <= o <= 0xD7A3: counts["ko"] += 1   # Hangul syllables
        elif 0x0600 <= o <= 0x06FF: counts["ar"] += 1   # Arabic
        elif (0x0041 <= o <= 0x005A) or (0x0061 <= o <= 0x007A): counts["en"] += 1
    total = sum(counts.values())
    if total < 15:
        return fallback
    dominant = max(counts, key=lambda k: counts[k])
    if counts[dominant] / total < 0.4:
        return fallback
    return dominant


async def revise_story_text(prev_title: str, prev_text: str, instruction: str,
                            original_context: str = "", story_id: int = None,
                            locale: str | None = None) -> dict:
    """Re-write an existing story incorporating a user's revision request.

    Differs from generate_story_text by giving the LLM the previous draft as
    context — so 'сделай короче' actually shortens the same story rather than
    inventing a new one. Falls back to a sensible inline prompt if no config
    key is set.
    """
    from db.config_manager import cfg
    default = (
        "Ниже — предыдущий вариант сказки и просьба пользователя её изменить.\n"
        "Перепиши сказку, учитывая просьбу. Сохрани суть, героев и тёплый детский тон. "
        "Первой строкой укажи ЗАГОЛОВОК: <название>.\n\n"
        "Исходная тема:\n{context}\n\n"
        "Предыдущий заголовок: {prev_title}\n"
        "Предыдущая сказка:\n{prev_text}\n\n"
        "Просьба от пользователя:\n{instruction}\n"
    )
    prompt_template = await cfg.get("prompt.story_revise", default)
    system = await cfg.get("prompt.story_text_system", "Ты — талантливый детский писатель.")
    # Override the visit-locale with the language the story is actually written
    # in — otherwise a Russian-topic-on-/en order silently flips to English here.
    effective_locale = _detect_story_locale(prev_text, locale)
    if effective_locale != locale:
        logger.info("Revise locale override: %r → %r (based on prev_text script)",
                    locale, effective_locale)
    prompt = _i18n_prefix(effective_locale) + prompt_template.format(
        context=(original_context or "").strip()[:1500],
        prev_title=(prev_title or "").strip()[:200],
        prev_text=(prev_text or "").strip()[:6000],
        instruction=(instruction or "").strip()[:1000],
    )
    response = await _call_llm(system=system, user=prompt, story_id=story_id, purpose="story_text")
    if not response or not response.strip():
        raise RuntimeError("Empty revised story response")
    fallback = prev_title or ("Bedtime story" if (effective_locale and effective_locale != "ru") else "Сказка на ночь")
    title, text = _extract_title(response, fallback)
    if len(text) > 15000:
        text = text[:15000]
    logger.info("Story revised [%s]: '%s', %d chars (was %d)", effective_locale or "ru", title, len(text), len(prev_text or ""))
    return {"title": title, "text": text}


async def convert_to_screenplay(title: str, text: str, story_id: int = None,
                                locale: str | None = None) -> dict:
    """Convert plain text story into structured screenplay JSON for TTS.

    When `locale` is set, the LLM is told to keep segment text in that language
    (it might otherwise summarise/translate by mistake).

    Returns:
        Dict with keys: title, characters, segments, scenes.
    """
    from db.config_manager import cfg
    prompt_template = await cfg.get("prompt.screenplay_convert", "Преобразуй текст в JSON.\n{text}")
    system = await cfg.get("prompt.screenplay_convert_system",
                            "Ты генерируешь ТОЛЬКО валидный JSON.")
    i18n = ""
    if locale and locale != "ru":
        lang = _LOCALE_LANG_NAME.get(locale, "English")
        i18n = (
            f"IMPORTANT: The input story is in {lang}. Keep ALL segment 'text' "
            f"fields verbatim in {lang}. Do NOT translate to English or any other language.\n\n"
        )
    prompt = i18n + prompt_template.format(title=title, text=text[:8000])

    for attempt in range(1, 4):
        response = await _call_llm(
            system=system,
            user=prompt,
            story_id=story_id,
            purpose="screenplay_convert",
        )
        logger.info("Screenplay convert response (attempt %d): length=%d",
                     attempt, len(response) if response else 0)

        if not response or not response.strip():
            continue

        try:
            screenplay = _extract_json(response)
            break
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("Screenplay convert JSON parse failed (attempt %d): %s", attempt, e)
            if attempt == 3:
                raise

    # Validate
    required = {"title", "characters", "segments"}
    missing = required - set(screenplay.keys())
    if missing:
        raise ValueError(f"Screenplay missing fields: {missing}")

    char_ids = {c["id"] for c in screenplay["characters"]}
    if "narrator" not in char_ids:
        raise ValueError("Screenplay must have a 'narrator' character")

    # Enforce segment limit
    if len(screenplay["segments"]) > 60:
        logger.warning("Screenplay has %d segments, truncating to 60", len(screenplay["segments"]))
        screenplay["segments"] = screenplay["segments"][:60]

    # Split long segments by sentence boundaries instead of truncating
    new_segments = []
    for seg in screenplay["segments"]:
        if seg.get("character_id") not in char_ids:
            seg["character_id"] = "narrator"
        text = seg.get("text", "")
        if len(text) <= 250:
            new_segments.append(seg)
        else:
            # Split by sentence boundaries
            import re as _re
            sentences = _re.split(r'(?<=[.!?])\s+', text)
            current = ""
            for sentence in sentences:
                if len(current) + len(sentence) + 1 > 250 and current:
                    new_seg = dict(seg)
                    new_seg["text"] = current.strip()
                    new_segments.append(new_seg)
                    current = sentence
                else:
                    current = (current + " " + sentence).strip() if current else sentence
            if current.strip():
                new_seg = dict(seg)
                new_seg["text"] = current.strip()
                new_segments.append(new_seg)
    screenplay["segments"] = new_segments

    logger.info("Screenplay converted: '%s', %d characters, %d segments",
                screenplay["title"], len(screenplay["characters"]), len(screenplay["segments"]))
    return screenplay
