#!/usr/bin/env python3
"""Translate Privacy + Terms content blocks to 10 non-EN locales via Gemini.

Output: /app/web/locales_legal/{locale}.json with {privacy_html, terms_html} per locale.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, "/app")

import aiohttp

OUT = Path("/opt/skazka-bot/web/locales_legal")
OUT.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("legal-translate")

LANGS = [
    ("de", "German"), ("es", "Spanish"), ("fr", "French"), ("it", "Italian"),
    ("pl", "Polish"), ("pt-BR", "Brazilian Portuguese"), ("tr", "Turkish"),
    ("ja", "Japanese"), ("ko", "Korean"), ("ar", "Modern Standard Arabic"),
]

# Master legal blocks — extracted from the EN legal.html.
# Use {{key}} for sections so we can render via template.
PRIVACY_EN = {
    "title": "Privacy Policy",
    "lead": "Effective 2026-06-07 · Available in your language",
    "h_collect": "What we collect",
    "collect_li": [
        "<b>Email address</b> — to send you the generated story.",
        "<b>Story topic and child's name</b> — only as input to the AI for generation.",
        "<b>Photo of your child</b> (optional) — used to make the hero look like your child in the illustrations.",
        "<b>Payment metadata</b> — handled by our payment processor (YooKassa / FastSpring). We do not store card numbers.",
        "<b>Anonymous analytics</b> — Cloudflare anonymised request logs.",
    ],
    "h_use": "How we use your data",
    "use_li": [
        "To generate the personalised story you requested.",
        "To deliver the finished video and audio to you by email.",
        "To respond to support requests.",
        "To improve our service in aggregate, anonymised form.",
    ],
    "h_photos": "Photos of children",
    "p_photos": "Photos are used <b>only</b> for the character reference in your story's illustrations. We do not publish them, share them, or use them to train other AI models. You can request deletion at any time by emailing us.",
    "h_retention": "Data retention",
    "p_retention": "Your story files (audio, video, illustrations) are stored for 12 months so you can re-download them. After that, files are deleted. Account email is kept for the same period unless you request earlier deletion.",
    "h_rights": "Your rights",
    "rights_li": [
        "Request a copy of your data.",
        "Request correction or deletion of your data.",
        "Withdraw consent at any time.",
        "Contact a supervisory authority (in your country) if you believe your rights have been violated.",
    ],
    "h_contact": "Contact",
    "p_contact": 'For any privacy questions, email <a href="mailto:hello@lalaka.ai">hello@lalaka.ai</a>.',
    "note": "Lalaka is operated by «СААС» LLC (Russia). EU/US users are protected under standard GDPR/CCPA principles; we process data only as needed for the service.",
}

TERMS_EN = {
    "title": "Terms of Service",
    "lead": "Effective 2026-06-07 · Available in your language",
    "h_what": "What Lalaka does",
    "p_what": "Lalaka generates a personalised audio fairy tale from your input (topic, child's name, age, optional photo). The output is a short audio file with illustrations and a video. The service is provided \"as is\" — we make our best effort to produce a quality result but cannot guarantee specific creative outcomes.",
    "h_pricing": "Pricing and refunds",
    "p_pricing": 'You see the price before paying. If you are not happy with the result, contact us within 7 days at <a href="mailto:hello@lalaka.ai">hello@lalaka.ai</a> and we will either rewrite the story for free or refund the payment, our choice.',
    "h_content": "Content rules",
    "p_content": "You may not request content that is illegal, sexual, hateful, or describes real harm to real people. We may decline to generate stories that violate these rules. If we decline after payment, we will refund.",
    "h_ip": "Intellectual property",
    "p_ip": "You own the personalised story we generate for you. You may use it for personal and family enjoyment, share it with friends and relatives, and include it in family albums. You may not resell it or use it commercially without our written permission.",
    "h_photo": "Photo of your child",
    "p_photo": "By uploading a photo, you confirm you are the parent or legal guardian and consent to its use solely for character illustration in your story (see Privacy Policy).",
    "h_avail": "Service availability",
    "p_avail": "We do our best to keep Lalaka running. Occasional downtime may occur for maintenance. We are not liable for losses caused by interruptions to the service.",
    "h_changes": "Changes to these terms",
    "p_changes": "We may update these terms; we will display the new effective date at the top of this page. Continued use of Lalaka after a change means you accept the new terms.",
    "h_contact": "Contact",
    "p_contact": 'For support or terms questions, email <a href="mailto:hello@lalaka.ai">hello@lalaka.ai</a>.',
}

# Save EN as baseline
(OUT / "en.json").write_text(json.dumps({"privacy": PRIVACY_EN, "terms": TERMS_EN},
                                        ensure_ascii=False, indent=2), encoding="utf-8")
logger.info("wrote en.json (baseline)")


async def translate_locale(loc_code: str, lang_name: str):
    out_path = OUT / f"{loc_code}.json"
    if out_path.exists():
        logger.info(f"  {loc_code}: skip (exists)")
        return
    api = os.environ["OPENROUTER_API_KEY"]
    en_doc = {"privacy": PRIVACY_EN, "terms": TERMS_EN}
    prompt = (
        f"Translate the following Lalaka legal text JSON into {lang_name}. "
        "Translate ALL string values. Keep the JSON structure intact. "
        "Preserve HTML tags verbatim (e.g. <b>, <a href=...>). "
        "Preserve brand names: Lalaka, YooKassa, FastSpring, GDPR, CCPA, СААС. "
        "Preserve email addresses verbatim. Use natural legal/privacy register.\n\n"
        f"INPUT:\n{json.dumps(en_doc, ensure_ascii=False, indent=2)}\n\n"
        f"Return ONLY the translated JSON object, no markdown, no commentary."
    )
    body = {
        "model": "google/gemini-2.5-flash",
        "messages": [
            {"role": "system", "content": f"You are an expert legal translator for {lang_name}. Output strict JSON."},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 6000,
        "temperature": 0.15,
    }
    async with aiohttp.ClientSession() as s:
        async with s.post("https://openrouter.ai/api/v1/chat/completions",
                          json=body,
                          headers={"Authorization": f"Bearer {api}"},
                          timeout=aiohttp.ClientTimeout(total=180)) as r:
            data = await r.json()
    try:
        content = data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError):
        logger.error(f"{loc_code}: bad LLM response: {str(data)[:200]}")
        return
    s = content
    if s.startswith("```"):
        s = s.split("```", 2)[1]
        if s.startswith("json"):
            s = s[4:]
        s = s.strip("` \n")
    try:
        translated = json.loads(s)
    except json.JSONDecodeError as e:
        logger.error(f"{loc_code}: JSON parse failed: {e}")
        return
    out_path.write_text(json.dumps(translated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logger.info(f"  {loc_code}: ✓")


async def main():
    for loc, lang in LANGS:
        try:
            await translate_locale(loc, lang)
        except Exception as e:
            logger.error(f"{loc}: {e}", exc_info=True)


if __name__ == "__main__":
    asyncio.run(main())
