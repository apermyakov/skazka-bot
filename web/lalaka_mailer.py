# -*- coding: utf-8 -*-
"""Localized email senders for Lalaka — separate from skazik's mailer (Russian-only).

Each function picks the right locale from the order's saved locale + uses
translation keys (email_text_ready_*, email_story_ready_*, email_rate_*)
already present in web/locales/{locale}.json.

Reuses skazik's web/email_queue + UniSender Go / SMTP infra (just composes
locale-appropriate subject + HTML).
"""
from __future__ import annotations

from typing import Optional


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _render_html(title_block: str, body_block: str, cta_block: str, perk_block: str | None,
                 foot_block: str | None, team_line: str, addr_line: str, unsub_line: str) -> str:
    """Shared HTML scaffold for all lalaka emails."""
    perk_html = (
        f'<tr><td style="padding:14px 16px;background:#f5efff;border-radius:12px;'
        f'color:#3a2f6b;font-size:14px;line-height:1.55">{perk_block}</td></tr>'
    ) if perk_block else ""
    foot_html = (
        f'<tr><td style="padding:14px 0 0;border-top:1px solid #ece6fb;color:#6b6390;font-size:13px">{foot_block}</td></tr>'
    ) if foot_block else ""
    return (
        '<!DOCTYPE html><html><body style="margin:0;padding:0;background:#fbf7ff;'
        'font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:#241c44;line-height:1.55">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="background:#fbf7ff;padding:24px 12px"><tr><td align="center">'
        '<table role="presentation" width="560" cellpadding="0" cellspacing="0" border="0" '
        'style="max-width:560px;background:#ffffff;border-radius:18px;padding:28px 26px;'
        'box-shadow:0 6px 22px rgba(43,35,80,.06)">'
        '<tr><td style="padding:0 0 14px"><a href="https://lalaka.ai/" style="text-decoration:none;display:inline-block">'
        '<img src="https://lalaka.ai/static/lalaka_wordmark_transparent.png" alt="Lalaka" height="42" '
        'style="display:block;height:42px;width:auto"></a></td></tr>'
        f'<tr><td style="padding:0 0 6px;font-size:22px;font-weight:800;line-height:1.25">{title_block}</td></tr>'
        f'<tr><td style="padding:0 0 16px;color:#241c44">{body_block}</td></tr>'
        f'<tr><td style="padding:0 0 6px" align="center">{cta_block}</td></tr>'
        f'{perk_html}'
        f'{foot_html}'
        f'<tr><td style="padding:14px 0 0;color:#6b6390;font-size:13px">{team_line}</td></tr>'
        f'<tr><td style="padding:8px 0 0;color:#9d92be;font-size:12px;line-height:1.4">{addr_line}<br>{unsub_line}</td></tr>'
        '</table></td></tr></table></body></html>'
    )


def _cta_button(url: str, text: str) -> str:
    return (
        f'<a href="{_esc(url)}" '
        'style="display:inline-block;background:linear-gradient(135deg,#7c5cff,#ff7eb6);'
        'color:#ffffff;text-decoration:none;font-weight:800;font-size:17px;padding:14px 26px;'
        'border-radius:14px">' + _esc(text) + '</a>'
    )


def _t(locale: str, key: str, **fmt) -> str:
    """Look up translation from the in-memory _TRANSLATIONS dict in web.lalaka."""
    from web.lalaka import _TRANSLATIONS, DEFAULT_LOCALE
    val = _TRANSLATIONS.get(locale, {}).get(key) or _TRANSLATIONS.get(DEFAULT_LOCALE, {}).get(key) or ""
    if fmt:
        try:
            val = val.format(**fmt)
        except Exception:
            pass
    return val


async def _send(to_addr: str, subject: str, plain: str, html: str) -> bool:
    """Route via Resend (Western provider). Fallback to skazik's UniSender
    queue ONLY if Resend isn't configured yet — that way nothing breaks while
    DNS/keys are being set up, and once RESEND_API_KEY lands, all lalaka
    emails route through Resend automatically."""
    from web.lalaka_email_provider import _is_configured, send_via_resend
    if _is_configured():
        ok = await send_via_resend(to_addr, subject, plain, html)
        if ok:
            return True
        # If Resend fails (e.g. quota), fall through to queue so the email
        # at least lands somewhere instead of being silently dropped.
    from web.email_queue import enqueue_email
    await enqueue_email(to_addr, subject, plain, html)
    return True


async def send_text_ready(to_addr: str, title: str, order_url: str, locale: str, price: str) -> bool:
    """Free preview ready. Locale-aware."""
    subj = _t(locale, "email_text_ready_subject", title=title)
    h1   = _t(locale, "email_text_ready_h1")
    body = _t(locale, "email_text_ready_body", title=f"<b>{_esc(title)}</b>")
    cta  = _t(locale, "email_text_ready_cta")
    perks = _t(locale, "email_text_ready_perks", price=price)
    foot  = _t(locale, "email_text_ready_foot")
    team  = _t(locale, "email_footer_team")
    addr  = _t(locale, "email_footer_addr")
    unsub = _t(locale, "email_footer_unsub")
    html = _render_html(h1, body, _cta_button(order_url, cta), perks, foot, team, addr, unsub)
    plain = (
        f"{h1}\n\n«{title}»\n\n{body.replace('<b>','').replace('</b>','')}\n\n"
        f"{cta}: {order_url}\n\n{team}\n{addr}\n{unsub}"
    )
    return await _send(to_addr, subj, plain, html)


async def send_story_ready(to_addr: str, title: str, order_url: str, locale: str) -> bool:
    """Full audio+video story ready (after payment)."""
    subj = _t(locale, "email_story_ready_subject", title=title)
    h1   = _t(locale, "email_story_ready_h1")
    body = _t(locale, "email_story_ready_body", title=f"<b>{_esc(title)}</b>")
    cta  = _t(locale, "email_story_ready_cta")
    perks = _t(locale, "email_story_ready_perks")
    team  = _t(locale, "email_footer_team")
    addr  = _t(locale, "email_footer_addr")
    unsub = _t(locale, "email_footer_unsub")
    html = _render_html(h1, body, _cta_button(order_url, cta), perks, None, team, addr, unsub)
    plain = (
        f"{h1}\n\n«{title}»\n\n{body.replace('<b>','').replace('</b>','')}\n\n"
        f"{cta}: {order_url}\n\n{team}\n{addr}\n{unsub}"
    )
    return await _send(to_addr, subj, plain, html)


async def send_followup_rating(to_addr: str, title: str, order_url: str, locale: str) -> bool:
    """24h after delivery — rating ask. Use enqueue_followup_rating() instead for
    delivery scheduling — this immediate-send is kept for manual testing."""
    subj = _t(locale, "email_rate_subject", title=title)
    h1   = _t(locale, "email_rate_h1")
    body = _t(locale, "email_rate_body", title=f"<b>{_esc(title)}</b>")
    cta  = _t(locale, "email_rate_cta")
    team  = _t(locale, "email_footer_team")
    addr  = _t(locale, "email_footer_addr")
    unsub = _t(locale, "email_footer_unsub")
    html = _render_html(h1, body, _cta_button(order_url + "#rate", cta), None, None, team, addr, unsub)
    plain = (
        f"{h1}\n\n«{title}»\n\n{body.replace('<b>','').replace('</b>','')}\n\n"
        f"{cta}: {order_url}#rate\n\n{team}\n{addr}\n{unsub}"
    )
    return await _send(to_addr, subj, plain, html)


async def enqueue_followup_rating(to_addr: str, title: str, order_url: str, locale: str,
                                    send_at, meta: dict | None = None) -> int:
    """Enqueue the 24h follow-up rating ask for delayed delivery.

    Returns the email_outbox row id. The worker that picks the row up reads
    meta.type === 'lalaka_followup_rating' and skips delivery if the order's
    rating column is already non-null — that's how an early rater stops the
    "did you like it?" email from showing up after they already said yes."""
    subj = _t(locale, "email_rate_subject", title=title)
    h1   = _t(locale, "email_rate_h1")
    body = _t(locale, "email_rate_body", title=f"<b>{_esc(title)}</b>")
    cta  = _t(locale, "email_rate_cta")
    team  = _t(locale, "email_footer_team")
    addr  = _t(locale, "email_footer_addr")
    unsub = _t(locale, "email_footer_unsub")
    html = _render_html(h1, body, _cta_button(order_url + "#rate", cta), None, None, team, addr, unsub)
    plain = (
        f"{h1}\n\n«{title}»\n\n{body.replace('<b>','').replace('</b>','')}\n\n"
        f"{cta}: {order_url}#rate\n\n{team}\n{addr}\n{unsub}"
    )
    from web.email_queue import enqueue_email
    return await enqueue_email(to_addr, subj, plain, html, send_at=send_at, meta=meta)
