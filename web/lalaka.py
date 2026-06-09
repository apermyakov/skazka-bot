"""
Lalaka — international sibling of skazik.app.

Routes live behind a host-routing middleware in web/app.py that rewrites
lalaka.ai/<path> → /_lalaka/<path>. Skazik routes are not affected.

Phase 1 ends with: landing → create → preview → pay (ЮKassa) → generate → done.
ЮKassa charges RUB; we display local-currency prices via web/lalaka_pricing.
FastSpring will replace ЮKassa in a future phase.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Optional

import aiohttp
from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Request, UploadFile, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from html import escape as _escape_html
from fastapi.templating import Jinja2Templates

from web import yookassa_client
from web import lalaka_orders as L
from web.lalaka_pricing import price_for, PRICES
from web.format import format_story_html

logger = logging.getLogger("lalaka")

WEB_DIR = Path(__file__).parent
LOCALES_DIR = WEB_DIR / "locales"
UPLOAD_DIR = Path(os.environ.get("MEDIA_DIR", "./media")) / "_lalaka_uploads"

PUBLIC_BASE = "https://lalaka.ai"

# Lalaka serves 11 international locales. Russian/Ukrainian users → skazik.app.
SUPPORTED_LOCALES = ["en","de","es","fr","it","pl","pt-BR","tr","ja","ko","ar"]
DEFAULT_LOCALE = "en"
RTL_LOCALES = {"ar"}

COUNTRY_TO_LOCALE = {
    "DE": "de", "AT": "de", "CH": "de",
    "ES": "es", "MX": "es", "AR": "es", "CO": "es", "CL": "es", "PE": "es",
    "FR": "fr", "BE": "fr",
    "IT": "it",
    "PL": "pl",
    "BR": "pt-BR", "PT": "pt-BR",
    "TR": "tr",
    "JP": "ja",
    "KR": "ko",
    "SA": "ar", "AE": "ar", "EG": "ar", "MA": "ar", "DZ": "ar", "IQ": "ar", "JO": "ar", "KW": "ar", "LB": "ar", "QA": "ar", "TN": "ar",
    # RU/UA intentionally route to default (en) — those audiences belong on skazik.app
}

_TRANSLATIONS: dict[str, dict[str, str]] = {}
for loc in SUPPORTED_LOCALES:
    p = LOCALES_DIR / f"{loc}.json"
    _TRANSLATIONS[loc] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

LOCALE_NAMES = {
    "en":"English","de":"Deutsch","es":"Español","fr":"Français","it":"Italiano","pl":"Polski",
    "pt-BR":"Português (Brasil)","tr":"Türkçe","ja":"日本語","ko":"한국어","ar":"العربية",
}
LOCALE_FLAGS = {
    "en":"🇬🇧","de":"🇩🇪","es":"🇪🇸","fr":"🇫🇷","it":"🇮🇹","pl":"🇵🇱","pt-BR":"🇧🇷",
    "tr":"🇹🇷","ja":"🇯🇵","ko":"🇰🇷","ar":"🇸🇦",
}


# ── Locale detection ────────────────────────────────────────────────

def _normalize_lang_tag(tag: str) -> Optional[str]:
    if not tag:
        return None
    t = tag.strip().replace("_", "-")
    for loc in SUPPORTED_LOCALES:
        if t.lower() == loc.lower():
            return loc
    primary = t.split("-")[0].lower()
    for loc in SUPPORTED_LOCALES:
        if loc.lower() == primary or loc.lower().split("-")[0] == primary:
            return loc
    return None


def _parse_accept_language(header: str) -> list[str]:
    if not header:
        return []
    chosen: list[str] = []
    for part in header.split(","):
        token = part.split(";", 1)[0].strip()
        loc = _normalize_lang_tag(token)
        if loc and loc not in chosen:
            chosen.append(loc)
    return chosen


def detect_locale(request: Request, explicit: str | None = None) -> str:
    if explicit:
        loc = _normalize_lang_tag(explicit)
        if loc:
            return loc
    # URL path prefix
    path = request.url.path
    if path.startswith("/_lalaka"):
        path = path[len("/_lalaka"):] or "/"
    segments = [s for s in path.split("/") if s]
    if segments:
        loc = _normalize_lang_tag(segments[0])
        if loc:
            return loc
    # Query (?lang=de or ?locale=de — both accepted for deeplink friendliness)
    q = request.query_params.get("lang") or request.query_params.get("locale")
    if q:
        loc = _normalize_lang_tag(q)
        if loc:
            return loc
    # Cookie
    ck = request.cookies.get("lalaka_locale")
    if ck:
        loc = _normalize_lang_tag(ck)
        if loc:
            return loc
    # Accept-Language
    al = request.headers.get("accept-language", "")
    for loc in _parse_accept_language(al):
        return loc
    # CF country
    cc = request.headers.get("cf-ipcountry", "").upper()
    if cc in COUNTRY_TO_LOCALE:
        return COUNTRY_TO_LOCALE[cc]
    return DEFAULT_LOCALE


def t(locale: str, key: str, default: str | None = None) -> str:
    val = _TRANSLATIONS.get(locale, {}).get(key)
    if val:
        return val
    val = _TRANSLATIONS.get(DEFAULT_LOCALE, {}).get(key)
    if val:
        return val
    return default if default is not None else key


def _client_ip(request: Request) -> str:
    return (request.headers.get("cf-connecting-ip")
            or request.headers.get("x-real-ip")
            or (request.client.host if request.client else "0.0.0.0"))


def _build_context(request: Request, locale: str, extra: dict | None = None) -> dict:
    # Path users see (e.g. "/", "/de", "/create"). The middleware in app.py
    # rewrites the wire path to start with "/_lalaka"; strip that for canonical/hreflang.
    canon_path = request.url.path
    if canon_path.startswith("/_lalaka"):
        canon_path = canon_path[len("/_lalaka"):] or "/"
    ctx = {
        "locale": locale,
        "dir": "rtl" if locale in RTL_LOCALES else "ltr",
        "cf_analytics_token": os.environ.get("CF_WEB_ANALYTICS_TOKEN", ""),
        "t": _TRANSLATIONS.get(locale, _TRANSLATIONS[DEFAULT_LOCALE]),
        "supported_locales": SUPPORTED_LOCALES,
        "locale_names": LOCALE_NAMES,
        "locale_flags": LOCALE_FLAGS,
        "price": price_for(locale),
        # SEO/OG helpers
        "canonical_url": f"{PUBLIC_BASE}{canon_path}",
        "og_image": f"{PUBLIC_BASE}/static/lalaka_demos/og_lalaka.jpg",
        "current_year": 2026,
        # Demo video path for current locale. The ?v= cache-buster forces
        # CF/browser to fetch the latest MP4 after we regenerate demos.
        "demo_video": f"/static/lalaka_demos/{locale}.mp4?v=painted2026",
    }
    if extra:
        ctx.update(extra)
    return ctx


# ── Routes ──────────────────────────────────────────────────────────

lalaka_router = APIRouter()
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))


def _set_locale_cookie(resp, locale: str):
    resp.set_cookie("lalaka_locale", locale, max_age=365 * 86400, samesite="lax")
    return resp


# In-memory IP rate limiter for /create. We only need to stop a single bad
# actor from flooding the endpoint — a process-local dict is fine; a
# multi-worker setup would replace this with Redis, but the lalaka container
# runs uvicorn with workers=1.
_CREATE_RATE_BUCKET: dict[str, list[float]] = {}
_CREATE_RATE_WINDOW = 3600          # 1 hour
_CREATE_RATE_LIMIT = 5              # 5 fresh creates per hour per IP


def _client_ip(request: Request) -> str:
    return (
        request.headers.get("cf-connecting-ip")
        or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or (request.client.host if request.client else "0.0.0.0")
    )


def _create_rate_limited(request: Request) -> bool:
    """Return True if this IP has already burned its quota in the last hour."""
    ip = _client_ip(request)
    now = time.time()
    bucket = _CREATE_RATE_BUCKET.setdefault(ip, [])
    # Drop expired hits
    cutoff = now - _CREATE_RATE_WINDOW
    bucket[:] = [t for t in bucket if t > cutoff]
    if len(bucket) >= _CREATE_RATE_LIMIT:
        return True
    bucket.append(now)
    # Garbage-collect occasionally so the dict doesn't grow forever
    if len(_CREATE_RATE_BUCKET) > 5000:
        for k in list(_CREATE_RATE_BUCKET):
            if not _CREATE_RATE_BUCKET[k] or _CREATE_RATE_BUCKET[k][-1] < cutoff:
                _CREATE_RATE_BUCKET.pop(k, None)
    return False


async def _verify_recaptcha(token: str, remote_ip: str) -> bool:
    """Validate a Google reCAPTCHA v3 token. Returns True if score ≥ 0.5.

    Skip-verify when RECAPTCHA_SECRET isn't configured (dev/early-launch),
    so the captcha hooks can be wired into the form before a key is provisioned.
    """
    secret = os.environ.get("RECAPTCHA_SECRET", "").strip()
    if not secret:
        return True  # not enforced yet
    if not token:
        return False
    url = "https://www.google.com/recaptcha/api/siteverify"
    data = {"secret": secret, "response": token, "remoteip": remote_ip}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
            async with s.post(url, data=data) as r:
                if r.status != 200:
                    return False
                j = await r.json()
        if not j.get("success"):
            return False
        # v3 returns "score" (0.0-1.0). Below 0.5 = likely bot.
        score = float(j.get("score") or 0)
        return score >= 0.5
    except Exception:
        # Don't punish users for our network blip — treat as pass.
        return True


@lalaka_router.get("/", response_class=HTMLResponse)
async def home(request: Request, lang: str | None = None):
    loc = detect_locale(request, explicit=lang)
    ctx = _build_context(request, loc)
    return _set_locale_cookie(templates.TemplateResponse(request, "lalaka/landing.html", ctx), loc)


@lalaka_router.get("/health", response_class=JSONResponse)
async def health():
    return {"ok": True, "service": "lalaka", "locales": len(SUPPORTED_LOCALES)}


@lalaka_router.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt():
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /order/\n"
        f"Sitemap: {PUBLIC_BASE}/sitemap.xml\n"
    )
    return PlainTextResponse(body)


@lalaka_router.get("/sitemap.xml")
async def sitemap_xml():
    urls = [f"{PUBLIC_BASE}/"]
    for loc in SUPPORTED_LOCALES:
        urls.append(f"{PUBLIC_BASE}/{loc}")
    urls.extend([f"{PUBLIC_BASE}/create", f"{PUBLIC_BASE}/privacy", f"{PUBLIC_BASE}/terms"])
    items = []
    for u in urls:
        items.append(
            f"  <url><loc>{u}</loc><changefreq>weekly</changefreq><priority>0.8</priority></url>"
        )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(items) + "\n</urlset>\n"
    )
    return Response(content=body, media_type="application/xml")


@lalaka_router.get("/favicon.ico")
async def favicon_ico():
    # Redirect to the Lalaka L-with-heart app-icon.
    return RedirectResponse(url="/static/lalaka_favicon_32.png", status_code=301)


_LEGAL_DIR = WEB_DIR / "locales_legal"
_LEGAL_DOCS: dict[str, dict] = {}
for _f in (_LEGAL_DIR.glob("*.json") if _LEGAL_DIR.exists() else []):
    try:
        _LEGAL_DOCS[_f.stem] = json.loads(_f.read_text(encoding="utf-8"))
    except Exception:
        pass


def _legal_doc(locale: str, kind: str) -> dict:
    doc = _LEGAL_DOCS.get(locale) or _LEGAL_DOCS.get(DEFAULT_LOCALE, {})
    return doc.get(kind, {})


@lalaka_router.get("/privacy", response_class=HTMLResponse)
async def privacy_page(request: Request, lang: str | None = None):
    loc = detect_locale(request, explicit=lang)
    doc = _legal_doc(loc, "privacy")
    ctx = _build_context(request, loc, {
        "page_title_override": f"{doc.get('title','Privacy Policy')} — Lalaka",
        "legal_kind": "privacy",
        "legal": doc,
    })
    return _set_locale_cookie(templates.TemplateResponse(request, "lalaka/legal.html", ctx), loc)


@lalaka_router.get("/terms", response_class=HTMLResponse)
async def terms_page(request: Request, lang: str | None = None):
    loc = detect_locale(request, explicit=lang)
    doc = _legal_doc(loc, "terms")
    ctx = _build_context(request, loc, {
        "page_title_override": f"{doc.get('title','Terms of Service')} — Lalaka",
        "legal_kind": "terms",
        "legal": doc,
    })
    return _set_locale_cookie(templates.TemplateResponse(request, "lalaka/legal.html", ctx), loc)


@lalaka_router.get("/create", response_class=HTMLResponse)
async def create_form(request: Request, lang: str | None = None):
    loc = detect_locale(request, explicit=lang)
    ctx = _build_context(request, loc)
    return _set_locale_cookie(templates.TemplateResponse(request, "lalaka/create.html", ctx), loc)


_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")


@lalaka_router.post("/create")
async def create_submit(
    request: Request,
    background_tasks: BackgroundTasks,
    topic: str = Form(...),
    email: str = Form(...),
    locale: str = Form(DEFAULT_LOCALE),
    utm_source: str = Form(""),
    utm_medium: str = Form(""),
    utm_campaign: str = Form(""),
    utm_term: str = Form(""),
    utm_content: str = Form(""),
    ref: str = Form(""),
):
    loc = _normalize_lang_tag(locale) or detect_locale(request)
    topic = (topic or "").strip()[:2000]
    email = (email or "").strip().lower()[:200]
    if len(topic) < 10 or not _EMAIL_RE.match(email):
        # Re-render with error inline isn't crucial for v1; redirect to /create.
        return RedirectResponse(url="/create?err=1", status_code=303)

    utm = {
        "source": utm_source[:200] or None,
        "medium": utm_medium[:200] or None,
        "campaign": utm_campaign[:200] or None,
        "term": utm_term[:200] or None,
        "content": utm_content[:200] or None,
    }
    oid = await L.create_order(
        locale=loc, topic=topic, email=email,
        utm=utm,
        referrer=(ref or request.headers.get("referer", ""))[:500] or None,
        ip=_client_ip(request),
        user_agent=(request.headers.get("user-agent") or "")[:500],
    )
    # Save price snapshot at order time (insulates from later table changes)
    pr = price_for(loc)
    await L.update_order(oid, currency=pr["currency"], display_amount=pr["display_amount"], amount_rub=pr["amount_rub"])
    # Kick off free text generation in the background
    background_tasks.add_task(_compose_lalaka, oid, topic, loc)
    return RedirectResponse(url=f"/order/{oid}", status_code=303)


@lalaka_router.get("/order/{oid}", response_class=HTMLResponse)
async def order_page(request: Request, oid: str):
    o = await L.get_order(oid)
    if not o:
        raise HTTPException(404)
    loc = o.get("locale") or detect_locale(request)
    ctx = _build_context(request, loc, {"oid": oid})
    return _set_locale_cookie(templates.TemplateResponse(request, "lalaka/order.html", ctx), loc)


@lalaka_router.get("/order/{oid}/status")
async def order_status(oid: str):
    o = await L.get_order(oid)
    if not o:
        raise HTTPException(404)
    body = {
        "status": o.get("status"),
        "title": o.get("title"),
        "story_text": o.get("story_text"),
        "text_html": format_story_html(o.get("story_text") or "") if o.get("story_text") else "",
        "photo_path": o.get("photo_path"),
        "progress": o.get("progress"),
        "video_url": o.get("video_url"),
        "audio_url": o.get("audio_url"),
        "illustrations": o.get("illustrations"),
        "error": o.get("error"),
        "locale": o.get("locale"),
        "rating": o.get("rating"),
    }
    return JSONResponse(body)


@lalaka_router.post("/order/{oid}/edit")
async def order_edit(oid: str, background_tasks: BackgroundTasks, comment: str = Form(...)):
    o = await L.get_order(oid)
    if not o:
        raise HTTPException(404)
    if o.get("status") not in ("text_ready", "failed"):
        return JSONResponse({"ok": False, "error": "wrong_status"}, status_code=400)
    await L.update_order(oid, status="composing", progress=None)
    background_tasks.add_task(
        _revise_lalaka, oid, o.get("title") or "", o.get("story_text") or "",
        (comment or "").strip()[:1000], o.get("topic") or "", o.get("locale") or DEFAULT_LOCALE,
    )
    return {"ok": True}


@lalaka_router.get("/admin/stats", response_class=HTMLResponse)
async def admin_stats(request: Request, token: str = ""):
    """Token-gated KPI dashboard for Lalaka orders."""
    if not token or token != os.environ.get("ADMIN_TOKEN"):
        return HTMLResponse("Forbidden", status_code=403)
    import db.database as _dbmod
    pool = getattr(_dbmod, "_pool", None)
    if pool is None:
        return HTMLResponse("DB not ready", status_code=503)
    async with pool.acquire() as conn:
        by_status = await conn.fetch(
            "SELECT status, COUNT(*) AS n FROM lalaka_orders GROUP BY status ORDER BY n DESC"
        )
        by_locale = await conn.fetch(
            "SELECT locale, COUNT(*) AS total, "
            "COUNT(*) FILTER (WHERE status='text_ready') AS preview, "
            "COUNT(*) FILTER (WHERE paid_at IS NOT NULL) AS paid, "
            "COUNT(*) FILTER (WHERE status='done') AS done, "
            "COALESCE(SUM(display_amount) FILTER (WHERE paid_at IS NOT NULL), 0) AS revenue, "
            "COALESCE(MAX(currency) FILTER (WHERE paid_at IS NOT NULL), MAX(currency)) AS currency "
            "FROM lalaka_orders GROUP BY locale ORDER BY total DESC"
        )
        recent = await conn.fetch(
            "SELECT id, locale, status, title, email, currency, display_amount, paid_at, created_at "
            "FROM lalaka_orders ORDER BY created_at DESC LIMIT 25"
        )
        funnel = await conn.fetchrow(
            "SELECT COUNT(*) AS visits, "
            "COUNT(*) FILTER (WHERE status IN ('text_ready','awaiting_payment','generating','done')) AS preview_ready, "
            "COUNT(*) FILTER (WHERE paid_at IS NOT NULL) AS paid, "
            "COUNT(*) FILTER (WHERE status='done') AS delivered "
            "FROM lalaka_orders"
        )
    # Tiny HTML render
    def esc(s):
        return ("" if s is None else str(s)).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
    rows_locale = "".join(
        f"<tr><td>{esc(r['locale'])}</td><td>{r['total']}</td><td>{r['preview']}</td>"
        f"<td>{r['paid']}</td><td>{r['done']}</td>"
        f"<td>{r['revenue']:.2f} {esc(r['currency'] or '')}</td></tr>"
        for r in by_locale
    )
    rows_status = "".join(f"<tr><td>{esc(r['status'])}</td><td>{r['n']}</td></tr>" for r in by_status)
    rows_recent = "".join(
        f"<tr><td><code>{esc(r['id'][:10])}</code></td><td>{esc(r['locale'])}</td>"
        f"<td>{esc(r['status'])}</td><td>{esc((r['title'] or '')[:40])}</td>"
        f"<td>{esc(r['email'] or '—')}</td>"
        f"<td>{r['display_amount'] or '—'} {esc(r['currency'] or '')}</td>"
        f"<td>{esc(str(r['created_at']).split('.')[0])}</td></tr>"
        for r in recent
    )
    pv = max(funnel['visits'], 1)
    body = (
        f"<style>"
        f"body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#fbf7ff;color:#241c44;padding:24px;max-width:1280px;margin:0 auto}}"
        f"h1{{margin:0 0 18px;font-size:24px}}h2{{font-size:18px;margin:26px 0 10px}}"
        f"table{{border-collapse:collapse;background:#fff;border-radius:10px;overflow:hidden;width:100%;font-size:13.5px;margin-bottom:18px;box-shadow:0 4px 14px rgba(43,35,80,.06)}}"
        f"th,td{{padding:8px 12px;text-align:left;border-bottom:1px solid #f1ebff}}th{{background:#f1ebff;font-weight:700}}"
        f"code{{background:#f1ebff;padding:2px 5px;border-radius:4px;font-size:12px}}"
        f".kpi{{display:flex;gap:10px;flex-wrap:wrap;margin:0 0 18px}}"
        f".k{{flex:1 1 160px;background:#fff;border-radius:12px;padding:14px 16px;box-shadow:0 4px 14px rgba(43,35,80,.06)}}"
        f".k .v{{font-size:22px;font-weight:800;color:#7c5cff}}.k .l{{color:#6b6390;font-size:12.5px;font-weight:600;margin-top:2px}}"
        f"</style>"
        f"<h1>📊 Lalaka — admin stats</h1>"
        f"<div class='kpi'>"
        f"<div class='k'><div class='v'>{funnel['visits']}</div><div class='l'>orders created</div></div>"
        f"<div class='k'><div class='v'>{funnel['preview_ready']}</div><div class='l'>preview ready ({funnel['preview_ready']*100//pv}%)</div></div>"
        f"<div class='k'><div class='v'>{funnel['paid']}</div><div class='l'>paid ({funnel['paid']*100//pv}%)</div></div>"
        f"<div class='k'><div class='v'>{funnel['delivered']}</div><div class='l'>delivered ({funnel['delivered']*100//pv}%)</div></div>"
        f"</div>"
        f"<h2>By locale</h2>"
        f"<table><thead><tr><th>locale</th><th>total</th><th>preview</th><th>paid</th><th>done</th><th>revenue</th></tr></thead><tbody>{rows_locale}</tbody></table>"
        f"<h2>By status</h2>"
        f"<table><thead><tr><th>status</th><th>count</th></tr></thead><tbody>{rows_status}</tbody></table>"
        f"<h2>Recent 25 orders</h2>"
        f"<table><thead><tr><th>id</th><th>loc</th><th>status</th><th>title</th><th>email</th><th>price</th><th>created</th></tr></thead><tbody>{rows_recent}</tbody></table>"
    )
    return HTMLResponse(body)


# --- Voice browser (token-gated) ----------------------------------------------
_VOICE_SAMPLE_DIR = WEB_DIR / "static" / "voice_samples_cache"
_VOICE_SAMPLE_DIR.mkdir(parents=True, exist_ok=True)

# Short bedtime opening per locale (~10s narration).
_VOICE_SAMPLE_TEXT = {
    "en":    "Once upon a time, there lived a brave little child who loved to dream.",
    "de":    "Es war einmal ein mutiges kleines Kind, das gerne träumte.",
    "es":    "Érase una vez un niño valiente que amaba soñar despierto.",
    "fr":    "Il était une fois un petit enfant courageux qui aimait rêver.",
    "it":    "C'era una volta un coraggioso bambino che amava sognare.",
    "pl":    "Dawno, dawno temu żyło dzielne dziecko, które uwielbiało marzyć.",
    "pt-BR": "Era uma vez uma criança corajosa que adorava sonhar.",
    "tr":    "Bir zamanlar hayal kurmayı çok seven cesur bir çocuk varmış.",
    "ja":    "むかしむかし、夢を見るのが大好きな勇敢な子供がいました。",
    "ko":    "옛날 옛적에, 꿈꾸기를 사랑하는 용감한 아이가 살았어요.",
    "ar":    "كان يا ما كان في قديم الزمان طفل شجاع يحب الأحلام.",
}


@lalaka_router.get("/admin/voices", response_class=HTMLResponse)
async def admin_voices(request: Request, token: str = "", lang: str = ""):
    """All voices, grouped by locale. Click → on-demand sample."""
    if not token or token != os.environ.get("ADMIN_TOKEN"):
        return HTMLResponse("Forbidden", status_code=403)
    from engine.voice_pool_intl import get_voices_for_locale, CURATED_NARRATORS

    locales = ["en","de","es","fr","it","pl","pt-BR","tr","ja","ko","ar"]
    if lang and lang in locales:
        locales = [lang]

    sections = []
    for loc in locales:
        try:
            vs = await get_voices_for_locale(loc)
        except Exception as e:
            sections.append(f"<h2>{loc} — ERROR {_escape_html(str(e))}</h2>")
            continue
        curated_id = CURATED_NARRATORS.get(loc)
        # Curated voice first, then v3-verified, then alpha
        def sort_key(v):
            return (v.voice_id != curated_id, not v.is_v3_verified, v.name.lower())
        vs = sorted(vs, key=sort_key)
        cards = []
        for v in vs:
            is_curated = v.voice_id == curated_id
            badge = '<span class="badge curated">✨ CURATED</span>' if is_curated else ""
            v3 = '<span class="badge v3">v3</span>' if v.is_v3_verified else ""
            gender_icon = {"male":"♂","female":"♀"}.get(v.gender, "·")
            sample_url = f"/admin/voice_sample?voice_id={v.voice_id}&lang={loc}&token={token}"
            cards.append(
                f'<div class="card{" curated-card" if is_curated else ""}">'
                f'<div class="hdr">{badge}{v3} <b>{_escape_html(v.name)}</b></div>'
                f'<div class="meta">{gender_icon} {v.gender} · {v.age_group} · <em>{v.tone}</em></div>'
                f'<div class="vid">{v.voice_id}</div>'
                f'<button class="play" data-src="{sample_url}">▶ Listen</button>'
                f'<audio preload="none"></audio>'
                f'</div>'
            )
        cards_html = "".join(cards)
        sections.append(f'<section><h2>{loc.upper()} <span class="cnt">({len(vs)} voices)</span></h2>'
                        f'<div class="grid">{cards_html}</div></section>')

    nav = " · ".join(
        (f'<b>{l}</b>' if l == lang else f'<a href="/admin/voices?token={token}&lang={l}">{l}</a>')
        for l in ["all","en","de","es","fr","it","pl","pt-BR","tr","ja","ko","ar"]
    ).replace("?token={t}&lang=all".format(t=token), f"?token={token}")
    if not lang:
        nav = nav.replace("<a href=\"/admin/voices?token={t}&lang=all\">all</a>".format(t=token), "<b>all</b>")

    page = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Voice browser — Lalaka</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{{--ink:#241c44;--muted:#6b6390;--accent:#7c5cff;--bg:#fbf7ff;--line:#e4dcfb}}
body{{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;line-height:1.5}}
.wrap{{max-width:1200px;margin:0 auto;padding:18px 22px 60px}}
h1{{margin:8px 0 14px}}
h2{{margin:30px 0 10px;font-size:20px}}
.cnt{{color:var(--muted);font-size:14px;font-weight:500}}
.nav{{margin:6px 0 18px;padding:10px 0;border-bottom:1px solid var(--line);color:var(--muted);font-size:14px}}
.nav a{{color:var(--accent);text-decoration:none;padding:2px 4px}}
.nav b{{color:var(--ink);background:#e9e0ff;padding:2px 6px;border-radius:6px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:10px}}
.card{{background:#fff;border:1.5px solid var(--line);border-radius:12px;padding:12px 14px;display:flex;flex-direction:column;gap:6px}}
.card.curated-card{{border-color:#7c5cff;background:linear-gradient(135deg,#fff,#f6efff)}}
.hdr{{font-size:14.5px}}
.meta{{color:var(--muted);font-size:13px}}
.meta em{{color:var(--accent);font-style:normal;font-weight:600}}
.vid{{font-family:ui-monospace,Menlo,monospace;font-size:11.5px;color:#a59cc7;word-break:break-all}}
.badge{{display:inline-block;padding:2px 7px;border-radius:999px;font-size:11px;font-weight:700;margin-right:5px;line-height:1.3}}
.badge.curated{{background:#7c5cff;color:#fff}}
.badge.v3{{background:#dff5e4;color:#0d6e30}}
.play{{background:linear-gradient(135deg,#7c5cff,#ff7eb6);color:#fff;border:0;border-radius:10px;padding:8px 14px;font-weight:700;cursor:pointer;font-size:14px}}
.play:disabled{{opacity:.5;cursor:wait}}
.play.playing{{background:#a59cc7}}
</style>
</head><body>
<div class="wrap">
<h1>Voice browser</h1>
<div class="nav">filter: {nav}</div>
{"".join(sections)}
</div>
<script>
document.body.addEventListener("click", async function(e){{
  const btn = e.target.closest(".play");
  if(!btn) return;
  // Stop any other playing audio
  document.querySelectorAll("audio").forEach(a => {{ if(!a.paused){{ a.pause(); a.currentTime = 0; }} }});
  document.querySelectorAll(".play").forEach(b => b.classList.remove("playing"));
  const audio = btn.parentElement.querySelector("audio");
  if(!audio.src){{
    btn.disabled = true;
    btn.textContent = "⏳ generating…";
    try {{
      const r = await fetch(btn.dataset.src);
      if(!r.ok){{ throw new Error("HTTP " + r.status); }}
      const blob = await r.blob();
      audio.src = URL.createObjectURL(blob);
    }} catch(e){{
      btn.textContent = "✗ failed";
      btn.disabled = false;
      return;
    }}
    btn.disabled = false;
    btn.textContent = "▶ Listen";
  }}
  if(audio.paused){{ audio.play(); btn.classList.add("playing"); btn.textContent = "⏸ playing"; }}
  else {{ audio.pause(); btn.classList.remove("playing"); btn.textContent = "▶ Listen"; }}
  audio.onended = () => {{ btn.classList.remove("playing"); btn.textContent = "▶ Listen"; }};
}});
</script>
</body></html>"""
    return HTMLResponse(page)


@lalaka_router.get("/admin/voice_sample")
async def admin_voice_sample(request: Request, voice_id: str = "", lang: str = "en", token: str = ""):
    """On-demand TTS sample. Cached to disk by (voice_id, lang)."""
    if not token or token != os.environ.get("ADMIN_TOKEN"):
        return HTMLResponse("Forbidden", status_code=403)
    if not voice_id or "/" in voice_id or len(voice_id) > 64:
        return HTMLResponse("bad voice_id", status_code=400)
    text = _VOICE_SAMPLE_TEXT.get(lang) or _VOICE_SAMPLE_TEXT["en"]
    cache_path = _VOICE_SAMPLE_DIR / f"{voice_id}__{lang}.mp3"
    if not cache_path.exists():
        api_key = os.environ.get("ELEVENLABS_API_KEY", "")
        if not api_key:
            return HTMLResponse("no API key", status_code=500)
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        body = {"text": text, "model_id": "eleven_v3"}
        try:
            import aiohttp as _aio
            async with _aio.ClientSession() as s:
                async with s.post(url, json=body, headers={"xi-api-key": api_key},
                                  timeout=_aio.ClientTimeout(total=60)) as r:
                    if r.status != 200:
                        return HTMLResponse(f"eleven {r.status}: {(await r.text())[:200]}",
                                            status_code=502)
                    cache_path.write_bytes(await r.read())
        except Exception as e:
            return HTMLResponse(f"tts failed: {e}", status_code=502)
    return FileResponse(str(cache_path), media_type="audio/mpeg")


@lalaka_router.post("/transcribe", response_class=JSONResponse)
async def transcribe(request: Request, audio: UploadFile = File(...), locale: str = Form(DEFAULT_LOCALE)):
    """Locale-aware voice → text. Used by /create mic button."""
    loc = _normalize_lang_tag(locale) or DEFAULT_LOCALE
    lang_map = {
        "en": "English", "de": "German", "es": "Spanish", "fr": "French",
        "it": "Italian", "pl": "Polish", "pt-BR": "Brazilian Portuguese",
        "tr": "Turkish", "ja": "Japanese", "ko": "Korean", "ar": "Arabic",
    }
    lang_name = lang_map.get(loc, "English")
    try:
        import base64
        import tempfile
        # Read uploaded audio; ffmpeg-convert to MP3 (Gemini accepts mp3)
        raw = await audio.read()
        if len(raw) > 8 * 1024 * 1024:
            return JSONResponse({"ok": False, "error": "too_large"}, status_code=413)
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f:
            f.write(raw)
            in_path = f.name
        out_path = in_path.replace(".webm", ".mp3")
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", in_path, "-ar", "16000", "-ac", "1", "-b:a", "64k", out_path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        try:
            os.unlink(in_path)
        except Exception:
            pass
        if not os.path.exists(out_path):
            return JSONResponse({"ok": False, "error": "convert_failed"}, status_code=500)
        with open(out_path, "rb") as fh:
            mp3_b64 = base64.b64encode(fh.read()).decode("ascii")
        try:
            os.unlink(out_path)
        except Exception:
            pass
        api = os.environ["OPENROUTER_API_KEY"]
        body = {
            "model": "google/gemini-2.5-flash",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "input_audio", "input_audio": {"data": mp3_b64, "format": "mp3"}},
                    {"type": "text", "text": (
                        f"Transcribe this voice memo in {lang_name}. The speaker is describing a "
                        f"child for a personalised fairy tale — pay special attention to the child's "
                        f"name, age, and the story topic. Return ONLY the transcribed text in "
                        f"{lang_name}, no commentary."
                    )},
                ],
            }],
            "max_tokens": 500,
            "temperature": 0.1,
        }
        async with aiohttp.ClientSession() as s:
            async with s.post(
                "https://openrouter.ai/api/v1/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {api}"},
                timeout=aiohttp.ClientTimeout(total=60),
            ) as r:
                data = await r.json()
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
        if not text:
            return JSONResponse({"ok": False, "error": "empty"}, status_code=500)
        return {"ok": True, "text": text[:2000]}
    except Exception as e:
        logger.error("lalaka transcribe failed: %s", e, exc_info=True)
        return JSONResponse({"ok": False, "error": "internal"}, status_code=500)


@lalaka_router.post("/order/{oid}/rate")
async def order_rate(oid: str, rating: int = Form(...), comment: str = Form("")):
    o = await L.get_order(oid)
    if not o:
        raise HTTPException(404)
    try:
        rating = max(1, min(5, int(rating)))
    except Exception:
        return JSONResponse({"ok": False, "error": "bad_rating"}, status_code=400)
    await L.set_order_rating(oid, rating, (comment or "").strip()[:2000] or None)
    if rating <= 2:
        title = (o.get("title") or "?")[:80]
        loc = o.get("locale") or "?"
        await _notify_admin(
            f"🚨 Bad Lalaka rating ({rating}★) [{loc}] «{title}» — https://lalaka.ai/order/{oid}\n"
            f"comment: {(comment or '')[:300]}"
        )
    return {"ok": True, "rating": rating}


@lalaka_router.post("/order/{oid}/photo")
async def order_photo(oid: str, photo: UploadFile = File(...)):
    o = await L.get_order(oid)
    if not o:
        raise HTTPException(404)
    # Save into per-order dir; do not overwrite once paid.
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    ext = (photo.filename or "img.jpg").split(".")[-1].lower()
    if ext not in ("jpg", "jpeg", "png", "webp"):
        ext = "jpg"
    dest = UPLOAD_DIR / f"{oid}.{ext}"
    data = await photo.read()
    if len(data) > 10 * 1024 * 1024:
        return JSONResponse({"ok": False, "error": "too_large"}, status_code=400)
    dest.write_bytes(data)
    await L.update_order(oid, photo_path=str(dest))
    return {"ok": True}


@lalaka_router.post("/order/{oid}/pay")
async def order_pay(oid: str):
    o = await L.get_order(oid)
    if not o:
        raise HTTPException(404)
    if o.get("status") not in ("text_ready", "awaiting_payment"):
        return JSONResponse({"ok": False, "error": "wrong_status"}, status_code=400)
    pr = price_for(o.get("locale") or DEFAULT_LOCALE)
    rub = o.get("amount_rub") or pr["amount_rub"]
    return_url = f"{PUBLIC_BASE}/order/{oid}"
    try:
        resp = await yookassa_client.create_payment(
            amount_rub=rub,
            description=f"Lalaka story #{oid}",
            return_url=return_url,
            metadata={"lalaka_oid": oid, "locale": o.get("locale") or "en"},
        )
    except Exception as e:
        logger.error("lalaka pay create failed for %s: %s", oid, e)
        return JSONResponse({"ok": False, "error": "payment_failed"}, status_code=500)
    if not resp.get("id"):
        logger.warning("lalaka pay create: bad response oid=%s resp=%s", oid, resp)
        return JSONResponse({"ok": False, "error": "payment_failed"}, status_code=500)
    conf = (resp.get("confirmation") or {}).get("confirmation_url")
    await L.update_order(oid, status="awaiting_payment", payment_id=resp["id"])
    return {"ok": True, "confirmation_url": conf}


@lalaka_router.post("/yookassa/webhook/lalaka")
async def lalaka_webhook(request: Request, background_tasks: BackgroundTasks):
    """ЮKassa webhook for Lalaka orders. Separate path from skazik so the two
    flows never conflate."""
    body = await request.json()
    obj = (body or {}).get("object") or {}
    md = obj.get("metadata") or {}
    oid = md.get("lalaka_oid")
    pid = obj.get("id")
    if not oid or not pid:
        return {"ok": False, "error": "no_oid"}
    if obj.get("status") != "succeeded" or not obj.get("paid"):
        logger.info("lalaka webhook non-paid for %s: status=%s paid=%s", oid, obj.get("status"), obj.get("paid"))
        return {"ok": True}
    claimed = await L.claim_for_generation(oid, pid)
    if claimed:
        background_tasks.add_task(_generate_lalaka, oid)
    return {"ok": True}


# Catch-all for locale prefixes /de, /ja, /pt-BR, etc.
@lalaka_router.get("/{locale_path}", response_class=HTMLResponse)
async def locale_or_fallback(request: Request, locale_path: str):
    loc = _normalize_lang_tag(locale_path)
    if not loc:
        raise HTTPException(404)
    ctx = _build_context(request, loc)
    return _set_locale_cookie(templates.TemplateResponse(request, "lalaka/landing.html", ctx), loc)


# Legacy /waitlist endpoint kept so prior email captures still flow.
@lalaka_router.post("/waitlist", response_class=JSONResponse)
async def waitlist(request: Request, email: str = Form(...), locale: str = Form(DEFAULT_LOCALE)):
    email = (email or "").strip().lower()[:200]
    if not _EMAIL_RE.match(email):
        return JSONResponse({"ok": False, "error": "invalid_email"}, status_code=400)
    loc = _normalize_lang_tag(locale) or DEFAULT_LOCALE
    try:
        import db.database as _dbmod
        pool = getattr(_dbmod, "_pool", None)
    except Exception:
        pool = None
    saved = False
    if pool is not None:
        try:
            async with pool.acquire() as conn:
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS lalaka_waitlist (
                        id BIGSERIAL PRIMARY KEY, email TEXT NOT NULL, locale TEXT NOT NULL,
                        ip TEXT, ua TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        UNIQUE(email))""")
                ip = _client_ip(request)
                ua = (request.headers.get("user-agent") or "")[:500]
                await conn.execute(
                    "INSERT INTO lalaka_waitlist (email, locale, ip, ua) VALUES ($1,$2,$3,$4) "
                    "ON CONFLICT (email) DO UPDATE SET locale = EXCLUDED.locale",
                    email, loc, ip, ua)
                saved = True
        except Exception:
            saved = False
    return JSONResponse({"ok": True, "saved": saved, "locale": loc})


# ── Background workers ──────────────────────────────────────────────

async def _compose_lalaka(oid: str, topic: str, locale: str):
    """Generate the free text preview. Mirrors skazik _compose but uses lalaka_orders + locale."""
    from engine.llm_client import generate_story_text
    try:
        res = await generate_story_text(topic, locale=locale)
        await L.update_order(oid, status="text_ready", title=res["title"], story_text=res["text"])
        logger.info("lalaka order %s [%s]: text ready '%s'", oid, locale, res["title"])
        # Locale-aware email to the buyer (tab-close safety net)
        order = await L.get_order(oid)
        buyer_email = (order or {}).get("email")
        if buyer_email:
            try:
                from web import lalaka_mailer
                price = price_for(locale)["display"]
                await lalaka_mailer.send_text_ready(
                    buyer_email, res["title"], f"{PUBLIC_BASE}/order/{oid}", locale, price,
                )
            except Exception as e:
                logger.warning("lalaka text_ready email %s: %s", oid, e)
        # Admin ping
        await _notify_admin(f"📝 Lalaka text ready [{locale}]: «{res['title']}»\nhttps://lalaka.ai/order/{oid}")
    except Exception as e:
        logger.error("lalaka compose %s failed: %s", oid, e, exc_info=True)
        await L.update_order(oid, status="failed", error=str(e)[:500])
        await _notify_admin(f"⚠️ Lalaka text failed [{locale}] {oid}: {str(e)[:200]}")


async def _revise_lalaka(oid: str, prev_title: str, prev_text: str,
                         instruction: str, original_topic: str, locale: str):
    from engine.llm_client import revise_story_text
    try:
        res = await revise_story_text(prev_title, prev_text, instruction, original_topic, locale=locale)
        await L.update_order(oid, status="text_ready", title=res["title"], story_text=res["text"])
        logger.info("lalaka order %s revised: '%s'", oid, res["title"])
    except Exception as e:
        logger.error("lalaka revise %s failed: %s", oid, e, exc_info=True)
        await L.update_order(oid, status="failed", error=str(e)[:500])


async def _generate_lalaka(oid: str):
    """Run TTS + illustrations + video. Mirrors skazik _generate but uses lalaka_orders + locale."""
    o = await L.get_order(oid)
    if not o:
        return
    locale = o.get("locale") or DEFAULT_LOCALE
    try:
        from engine.llm_client import convert_to_screenplay
        from engine.pipeline import generate_fairytale

        photos: list[str] = []
        if o.get("photo_path") and Path(o["photo_path"]).exists():
            photos = [base64.b64encode(Path(o["photo_path"]).read_bytes()).decode()]

        async def on_status(msg: str):
            await L.update_order(oid, progress=msg)

        screenplay = await convert_to_screenplay(o["title"], o["story_text"], locale=locale)
        result = await generate_fairytale(
            context=o["topic"], screenplay=screenplay,
            reference_photo_b64=(photos[0] if photos else None),
            reference_photos=photos,
            tempo=1.15, style="painted",
            locale=locale,
            on_status=on_status,
        )
        mid = result.get("order_id")
        def url(p):
            p = str(p).replace("/app/", "/")
            if not p.startswith("/"):
                p = "/" + p
            return p
        illus = [url(p) for p in result.get("illustrations", []) if p]
        await L.update_order(
            oid, status="done", media_order_id=mid, error=None,
            video_url=(f"/media/{mid}/fairytale.mp4" if result.get("video_path") else None),
            audio_url=(url(result["file_path"]) if result.get("file_path") else None),
            illustrations=illus)
        logger.info("lalaka order %s: generation done '%s'", oid, result.get("title"))
        # Locale-aware story-ready email to the buyer
        buyer_email = (o.get("email") or "").strip()
        if buyer_email:
            try:
                from web import lalaka_mailer
                await lalaka_mailer.send_story_ready(
                    buyer_email, result.get("title", ""),
                    f"{PUBLIC_BASE}/order/{oid}", locale,
                )
            except Exception as e:
                logger.warning("lalaka story_ready email %s: %s", oid, e)
        await _notify_admin(f"✅ Lalaka story DONE [{locale}] «{result.get('title')}»\nhttps://lalaka.ai/order/{oid}")
    except Exception as e:
        logger.error("lalaka generate %s failed: %s", oid, e, exc_info=True)
        await L.update_order(oid, status="failed", error=str(e)[:500])
        await _notify_admin(f"⚠️ Lalaka gen failed [{locale}] {oid}: {str(e)[:200]}")


async def _notify_admin(text: str):
    token = os.environ.get("BOT_TOKEN", "")
    admins = os.environ.get("ADMIN_IDS", "")
    if not token or not admins:
        return
    async with aiohttp.ClientSession() as s:
        for aid in admins.split(","):
            aid = aid.strip()
            if not aid:
                continue
            try:
                await s.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": aid, "text": text, "disable_web_page_preview": True},
                    timeout=aiohttp.ClientTimeout(total=10),
                )
            except Exception:
                pass
