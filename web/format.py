# -*- coding: utf-8 -*-
"""Format a raw generated story into clean HTML paragraphs for the web preview.

Mirrors the bot's display cleaning (bot/handlers/utils.py::_clean_for_display):
drops [audio tags], strips 'Рассказчик:' prefixes, turns 'Имя: реплика' into
'— реплика', then wraps each line as a <p>."""
import html as _html
import re


def format_story_html(text: str) -> str:
    if not text:
        return ""
    text = text.encode("utf-8", "replace").decode("utf-8")  # strip surrogates
    paras = []
    for raw in text.split("\n"):
        line = re.sub(r"\[[\w\s]+\]", "", raw).strip()
        if not line:
            continue
        if ":" in line:
            prefix, _, rest = line.partition(":")
            if len(prefix) < 30 and rest.strip():
                if prefix.strip().lower() == "рассказчик":
                    line = rest.strip()
                else:
                    line = "— " + rest.strip()
        if line:
            paras.append("<p>" + _html.escape(line) + "</p>")
    return "\n".join(paras)
