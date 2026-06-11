# -*- coding: utf-8 -*-
"""FastAPI web app for skazik.app.

Flow: form (topic + photo) → free TEXT preview → pay 999₽ → full generation
(TTS + illustrations + video) → delivery. Reuses the bot's engine/ pipeline,
Postgres pool and dynamic config. Payment is stubbed until the YuKassa key arrives.
"""
import asyncio
import base64
import logging
import os
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp
from fastapi import FastAPI, Request, Form, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import db.database as db_mod
from db.database import init_db
from db.config_manager import cfg
from web.orders import init_orders, create_order, get_order, update_order, claim_for_generation, create_feedback, count_orders_by_ip, set_order_rating, mark_followup_sent
from web import yookassa_client
from web.format import format_story_html

PUBLIC_BASE = "https://skazik.app"

METRIKA = """<!-- Yandex.Metrika counter -->
<script type="text/javascript">
    (function(m,e,t,r,i,k,a){
        m[i]=m[i]||function(){(m[i].a=m[i].a||[]).push(arguments)};
        m[i].l=1*new Date();
        for (var j = 0; j < document.scripts.length; j++) {if (document.scripts[j].src === r) { return; }}
        k=e.createElement(t),a=e.getElementsByTagName(t)[0],k.async=1,k.src=r,a.parentNode.insertBefore(k,a)
    })(window, document,'script','https://mc.yandex.ru/metrika/tag.js?id=109494799', 'ym');
    ym(109494799, 'init', {ssr:true, webvisor:true, clickmap:true, ecommerce:"dataLayer", referrer: document.referrer, url: location.href, accurateTrackBounce:true, trackLinks:true});
</script>
<noscript><div><img src="https://mc.yandex.ru/watch/109494799" style="position:absolute; left:-9999px;" alt="" /></div></noscript>
<!-- /Yandex.Metrika counter -->
<!-- Cookie/analytics consent notice (152-ФЗ informational; persists in localStorage) -->
<style>
  #ck-notice{position:fixed;left:12px;right:12px;bottom:12px;max-width:560px;margin:0 auto;
    background:#fff;color:#2b2350;border-radius:14px;padding:14px 16px;font:14px/1.45 -apple-system,Segoe UI,Roboto,Arial,sans-serif;
    box-shadow:0 10px 28px rgba(43,35,80,.16);border:1px solid #ece6fb;z-index:50;display:none}
  #ck-notice.on{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
  #ck-notice p{margin:0;flex:1;min-width:220px}
  #ck-notice a{color:#7c5cff}
  #ck-notice button{background:linear-gradient(135deg,#7c5cff,#ff7eb6);color:#fff;border:0;
    font-weight:700;font-size:14px;padding:9px 18px;border-radius:10px;cursor:pointer;font-family:inherit}
</style>
<div id="ck-notice" role="dialog" aria-label="Уведомление о cookies">
  <p>Сайт использует cookie и Яндекс.Метрику для аналитики. Подробнее в <a href="/privacy">политике</a>.</p>
  <button type="button" onclick="try{localStorage.setItem('ck_ok','1')}catch(e){};document.getElementById('ck-notice').classList.remove('on')">Хорошо</button>
</div>
<script>
(function(){
  try { if (!localStorage.getItem('ck_ok')) {
    document.addEventListener('DOMContentLoaded', function(){
      const n = document.getElementById('ck-notice'); if(n) n.classList.add('on');
      // Auto-dismiss as soon as the user starts interacting — scroll or any tap.
      // 152-ФЗ is informational, not consent-required, so this is compliant.
      function autoDismiss(){
        try{ localStorage.setItem('ck_ok','1'); }catch(e){}
        const el = document.getElementById('ck-notice');
        if(el) el.classList.remove('on');
      }
      window.addEventListener('scroll', autoDismiss, {once:true, passive:true});
      window.addEventListener('touchstart', autoDismiss, {once:true, passive:true});
      // Fallback: hide after 8 seconds regardless
      setTimeout(autoDismiss, 8000);
    });
  }} catch(e){}
})();
</script>"""

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger("skazik_web")

WEB_DIR = Path(__file__).parent
MEDIA_DIR = Path("/app/media")
UPLOAD_DIR = MEDIA_DIR / "_web_uploads"
MAX_PHOTO = 10 * 1024 * 1024

# Simple in-memory per-IP rate limiter for /create — protects free-preview LLM
# spend from bot abuse. Window 1h, configurable via limit.creates_per_hour_per_ip.
_create_log: dict[str, deque] = {}
_create_log_lock = asyncio.Lock()


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "0.0.0.0"


async def _check_rate_limit(ip: str, limit: int, window: int = 3600) -> bool:
    now = time.time()
    async with _create_log_lock:
        dq = _create_log.setdefault(ip, deque())
        while dq and dq[0] < now - window:
            dq.popleft()
        if len(dq) >= limit:
            return False
        dq.append(now)
        return True


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    cfg.set_pool(db_mod._pool)
    await cfg.seed_defaults()
    await init_orders()
    # Lalaka — separate orders table, additive only; skazik schema untouched.
    try:
        from web.lalaka_orders import init_lalaka_orders
        await init_lalaka_orders()
    except Exception as e:
        logger.warning("lalaka init failed: %s", e)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    # Email outbox: persistent queue + background worker
    try:
        from web.email_queue import init_email_queue, start_worker
        await init_email_queue()
        start_worker()
    except Exception as e:
        logger.warning("email queue init failed: %s", e)
    # Email inbox: IMAP poller for papa@skazik.app (silent no-op when creds unset)
    try:
        from web.inbox_poller import run_forever as run_inbox_poller
        async with db_mod._pool.acquire() as c:
            await c.execute(open("/app/scripts/init_inbox_schema.py").read().split('SCHEMA = """', 1)[1].split('"""', 1)[0])
        asyncio.create_task(run_inbox_poller())
    except Exception as e:
        logger.warning("inbox poller init failed: %s", e)
    # Abandoned-cart: remind 1h, mark abandoned at 24h
    try:
        from web.abandoned_cart import init_schema as init_cart, start_worker as start_cart
        await init_cart()
        start_cart(PUBLIC_BASE)
    except Exception as e:
        logger.warning("abandoned cart init failed: %s", e)
    # Resume both kinds of in-flight orders interrupted by restart/deploy/crash:
    # 'generating' (paid pipeline) and 'composing' (free text gen). Otherwise
    # they sit in the DB forever and the user sees a spinner that never finishes.
    try:
        async with db_mod._pool.acquire() as c:
            stuck_gen = await c.fetch("SELECT id FROM web_orders WHERE status='generating'")
            stuck_comp = await c.fetch("SELECT id, topic FROM web_orders WHERE status='composing'")
        for r in stuck_gen:
            asyncio.create_task(_generate(r["id"]))
        for r in stuck_comp:
            if r["topic"]:
                asyncio.create_task(_compose(r["id"], r["topic"]))
        if stuck_gen or stuck_comp:
            logger.info("resuming after restart: %d generating + %d composing",
                        len(stuck_gen), len(stuck_comp))
    except Exception as e:
        logger.warning("resume scan failed: %s", e)
    logger.info("skazik web started")
    yield


app = FastAPI(lifespan=lifespan, title="Сказик", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")
app.mount("/media", StaticFiles(directory=str(MEDIA_DIR)), name="media")


@app.middleware("http")
async def _noindex_uploads(request: Request, call_next):
    """User-uploaded photos must never appear in search engines."""
    resp = await call_next(request)
    p = request.url.path
    if p.startswith("/media/_web_uploads/"):
        resp.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive, noimageindex"
    return resp


_LALAKA_HOSTS = {"lalaka.ai", "www.lalaka.ai"}
_LALAKA_SHARED_PREFIXES = ("/static/", "/media/", "/_lalaka")


@app.middleware("http")
async def _lalaka_host_router(request: Request, call_next):
    """
    Route lalaka.ai/<path> → /_lalaka/<path> so skazik routes stay untouched.
    Shared static assets pass through unchanged.
    """
    host = (request.headers.get("host") or "").split(":")[0].lower()
    if host in _LALAKA_HOSTS:
        path = request.scope.get("path", "/") or "/"
        if not path.startswith(_LALAKA_SHARED_PREFIXES):
            request.scope["path"] = "/_lalaka" + path
            # raw_path is bytes; mirror the rewrite so downstream matches.
            if "raw_path" in request.scope and request.scope["raw_path"]:
                request.scope["raw_path"] = b"/_lalaka" + request.scope["raw_path"]
    return await call_next(request)


from web.lalaka import lalaka_router  # noqa: E402 — defined after app to avoid circular import
app.include_router(lalaka_router, prefix="/_lalaka")
from web.inbox_admin import router as inbox_router  # noqa: E402
app.include_router(inbox_router)


templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))
templates.env.globals["metrika"] = METRIKA


async def notify_admin(text: str):
    """Send a Telegram message to the admin(s) via the bot token (web-side)."""
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
                await s.post(f"https://api.telegram.org/bot{token}/sendMessage",
                             json={"chat_id": aid, "text": text, "disable_web_page_preview": True},
                             timeout=aiohttp.ClientTimeout(total=10))
            except Exception as e:
                logger.warning("notify_admin failed: %s", e)


def _is_llm_refusal(text: str) -> bool:
    """Detect when LLM declined to generate (e.g. content-policy block).
    Refusals usually start with 'I cannot' / 'Я не могу' in the first few hundred chars."""
    if not text:
        return False
    head = text[:500].lower()
    markers = (
        "я не могу исполнить", "я не могу выполнить", "я не могу сгенерировать",
        "я не буду", "я отказываюсь",
        "противоречит самой сути", "противоречит моим", "не соответствует моей роли",
        "против моих принципов", "против моих ценностей",
        "i cannot", "i'm not able", "i am not able", "i won't", "i will not",
        "i'm sorry, but i can", "as an ai", "i'm unable to",
    )
    return any(m in head for m in markers)


# ── background workers ──
async def _compose(oid: str, topic: str):
    from engine.llm_client import generate_story_text
    res = None
    last_err = None
    for attempt in (1, 2):
        try:
            res = await generate_story_text(topic)
            if _is_llm_refusal(res.get("text", "")):
                logger.warning("order %s: LLM REFUSAL detected on attempt %d (head=%r)",
                               oid, attempt, res["text"][:150])
                last_err = Exception("LLM_REFUSAL")
                res = None
                if attempt == 1:
                    await asyncio.sleep(1)
                continue
            if attempt > 1:
                logger.info("order %s: text gen succeeded on attempt %d", oid, attempt)
            break
        except Exception as e:
            last_err = e
            logger.warning("order %s compose attempt %d failed: %s", oid, attempt, e)
            if attempt == 1:
                await asyncio.sleep(2)
    if res is None:
        is_refusal = isinstance(last_err, Exception) and str(last_err) == "LLM_REFUSAL"
        err_msg = ("Не получилось сочинить на эту тему — попробуйте переформулировать (тема могла показаться нейросети неоднозначной)."
                   if is_refusal else str(last_err)[:500])
        logger.error("order %s compose failed after retries: %s%s", oid, last_err,
                     " [REFUSAL]" if is_refusal else "", exc_info=not is_refusal)
        await update_order(oid, status="failed", error=err_msg)
        if is_refusal:
            await notify_admin(f"⛔ LLM ОТКАЗАЛАСЬ генерить (заказ {oid})\nТема: {topic[:300]}")
        else:
            await notify_admin(f"⚠️ Текст НЕ сгенерировался (заказ {oid}): {str(last_err)[:200]}")
        return
    await update_order(oid, status="text_ready", title=res["title"], story_text=res["text"])
    logger.info("order %s: text ready (%s)", oid, res["title"])
    o = await get_order(oid)
    await notify_admin(f"📝 Текст сказки готов\n«{res['title']}»\nemail: {(o or {}).get('email') or '—'}\n{PUBLIC_BASE}/order/{oid}")
    # Tab-close safety net: instantly email the link so the user can return later.
    if o and o.get("email"):
        try:
            from web.mailer import send_text_ready_invite
            await send_text_ready_invite(o["email"], res["title"], f"{PUBLIC_BASE}/order/{oid}")
        except Exception as e:
            logger.warning("text_ready invite email for %s failed: %s", oid, e)


async def _generate(oid: str):
    o = await get_order(oid)
    if not o:
        return
    try:
        from engine.llm_client import convert_to_screenplay
        from engine.pipeline import generate_fairytale
        photos = []
        if o.get("photo_path") and Path(o["photo_path"]).exists():
            photos = [base64.b64encode(Path(o["photo_path"]).read_bytes()).decode()]
        async def on_status(msg: str):
            await update_order(oid, progress=msg)

        async def _run_gen():
            screenplay = await convert_to_screenplay(o["title"], o["story_text"])
            return await generate_fairytale(
                context=o["topic"], screenplay=screenplay,
                reference_photo_b64=(photos[0] if photos else None),
                reference_photos=photos, tempo=1.15, style="painted",
                on_status=on_status)

        # Auto-retry once on transient failure so users don't see "failed"
        # for hiccups in TTS/image APIs. Both attempts must throw before we give up.
        result = None
        last_err = None
        for attempt in (1, 2):
            try:
                result = await _run_gen()
                break
            except Exception as e:
                last_err = e
                logger.warning("order %s gen attempt %d failed: %s", oid, attempt, e)
                if attempt == 1:
                    await on_status("⚠️ Повторяю попытку...")
                    await asyncio.sleep(3)
        if result is None:
            raise last_err
        mid = result.get("order_id")
        def url(p):
            # media_dir is "./media" → paths come back relative ("media/...") or
            # absolute ("/app/media/..."). Normalise to a web path "/media/...".
            p = str(p).replace("/app/", "/")
            if not p.startswith("/"):
                p = "/" + p
            return p
        illus = [url(p) for p in result.get("illustrations", []) if p]
        # Paid orders without illustrations = partial failure. Mark failed so
        # user sees retry button instead of silently receiving MP3-only.
        if o.get("paid_at") and not illus:
            audio_p = url(result["file_path"]) if result.get("file_path") else None
            await update_order(
                oid, status="failed",
                error="Иллюстрации не сгенерировались — нажмите «Повторить»",
                media_order_id=mid,
                audio_url=audio_p)
            logger.warning("order %s: illustrations empty for PAID order — marked failed for retry", oid)
            await notify_admin(f"⚠️ Сказка БЕЗ иллюстраций (оплачено)\nЗаказ {oid} помечен failed — юзер увидит retry\n{PUBLIC_BASE}/order/{oid}")
            return
        await update_order(
            oid, status="done", media_order_id=mid, error=None,
            video_url=(f"/media/{mid}/fairytale.mp4" if result.get("video_path") else None),
            audio_url=(url(result["file_path"]) if result.get("file_path") else None),
            illustrations=illus)
        logger.info("order %s: generation done (%s)", oid, result.get("title"))
        vid = "видео" if result.get("video_path") else "только аудио"
        await notify_admin(f"✅ СКАЗКА ГОТОВА (оплачено)\n«{result.get('title')}» — {vid}\n{PUBLIC_BASE}/order/{oid}")
        buyer_email = (o.get("email") or "").strip()
        if buyer_email:
            from web.mailer import send_story_ready, send_followup_rating
            cover = None
            if illus and isinstance(illus, list) and illus:
                first = illus[0]
                cover = (PUBLIC_BASE + first) if isinstance(first, str) and first.startswith("/") else first
            asyncio.create_task(send_story_ready(
                buyer_email,
                result.get("title") or "Ваша сказка",
                f"{PUBLIC_BASE}/order/{oid}",
                cover_url=cover))
            # 24h follow-up: ask «понравилось ли?». Worker skips if already rated by then.
            if not o.get("followup_email_sent_at"):
                from datetime import datetime, timedelta, timezone
                delay_h = int(await cfg.get("followup.rating_delay_hours", 24))
                send_at = datetime.now(timezone.utc) + timedelta(hours=delay_h)
                try:
                    await send_followup_rating(
                        buyer_email,
                        result.get("title") or "ваша сказка",
                        oid,
                        send_at=send_at)
                    await mark_followup_sent(oid)
                except Exception as e:
                    logger.warning("followup enqueue failed for %s: %s", oid, e)
    except Exception as e:
        logger.error("order %s generate failed: %s", oid, e, exc_info=True)
        await update_order(oid, status="failed", error=str(e)[:500])
        await notify_admin(f"🔴 Сказка НЕ собралась после оплаты (заказ {oid}): {str(e)[:200]}")


# ── pages ──
@app.api_route("/health", methods=["GET", "HEAD"])
async def health():
    return JSONResponse({"ok": True})


@app.api_route("/healthz", methods=["GET", "HEAD"])
async def healthz():
    """Deep health: DB roundtrip + email queue not stuck + recent activity.
    Returns 200 if all green, 503 otherwise. For UptimeRobot / pinging."""
    status = {"db": False, "email_queue": True, "ok": True}
    try:
        async with db_mod._pool.acquire() as c:
            await c.fetchval("SELECT 1")
            status["db"] = True
            # Detect stuck email queue: queued rows overdue by >15 min
            stuck = await c.fetchval(
                "SELECT COUNT(*) FROM email_outbox "
                "WHERE status='queued' AND next_try_at < NOW() - INTERVAL '15 minutes'") or 0
            status["email_queue"] = stuck == 0
            if stuck:
                status["email_stuck_count"] = stuck
    except Exception as e:
        status["error"] = str(e)[:200]
        status["ok"] = False
    code = 200 if (status["db"] and status["email_queue"]) else 503
    status["ok"] = code == 200
    return JSONResponse(status, status_code=code)


@app.get("/yandex_d335fc8db349d42d.html", response_class=HTMLResponse)
async def yandex_webmaster_verify():
    """Site ownership confirmation file for Yandex.Webmaster.
    Code matches the existing DNS TXT 'yandex-verification' on skazik.app."""
    return HTMLResponse(
        '<html>\n    <head>\n'
        '        <meta http-equiv="Content-Type" content="text/html; charset=UTF-8">\n'
        '    </head>\n'
        '    <body>Verification: d335fc8db349d42d</body>\n'
        '</html>')


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots():
    return (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /order/\n"
        "Disallow: /admin/\n"
        "Disallow: /yookassa/\n"
        "Disallow: /healthz\n"
        "Disallow: /media/_web_uploads/\n"
        f"Sitemap: {PUBLIC_BASE}/sitemap.xml\n"
    )


@app.get("/sitemap.xml", response_class=Response)
async def sitemap():
    today = datetime.utcnow().strftime("%Y-%m-%d")
    urls = [
        ("/", "1.0", "weekly"),
        ("/create", "0.9", "weekly"),
        ("/sample", "0.8", "monthly"),
        ("/about", "0.7", "monthly"),
        ("/feedback", "0.4", "monthly"),
        ("/oferta", "0.3", "yearly"),
        ("/privacy", "0.3", "yearly"),
    ]
    items = "\n".join(
        f"  <url><loc>{PUBLIC_BASE}{u}</loc><lastmod>{today}</lastmod>"
        f"<changefreq>{cf}</changefreq><priority>{p}</priority></url>"
        for u, p, cf in urls
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{items}\n"
        '</urlset>\n'
    )
    return Response(content=xml, media_type="application/xml")


_stats_cache = {"v": None, "ts": 0.0}

async def _landing_stats() -> dict:
    """Cached counts for landing social proof (5-min TTL).
    `proof.base_count` config key is added to the real number — lets us start
    from a meaningful round number when launching ads, growing organically
    after that. Configurable without redeploy.
    """
    now = time.time()
    if _stats_cache["v"] and now - _stats_cache["ts"] < 300:
        return _stats_cache["v"]
    try:
        async with db_mod._pool.acquire() as c:
            bot_done = await c.fetchval("SELECT COUNT(*) FROM stories WHERE status='completed'") or 0
            web_done = await c.fetchval("SELECT COUNT(*) FROM web_orders WHERE status='done'") or 0
        real = int(bot_done) + int(web_done)
        base = int(await cfg.get("proof.base_count", 0))
        v = {"total": real + base}
    except Exception as e:
        logger.warning("landing stats failed: %s", e)
        v = {"total": 0}
    _stats_cache["v"] = v
    _stats_cache["ts"] = now
    return v


@app.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    price = await cfg.get("pricing.story_rub", 999)
    stats = await _landing_stats()
    return templates.TemplateResponse(request, "landing.html",
                                      {"price": price, "stories_total": stats["total"]})


@app.get("/about", response_class=HTMLResponse)
async def about(request: Request):
    return templates.TemplateResponse(request, "about.html", {})


@app.get("/night", response_class=HTMLResponse)
async def night(request: Request):
    return templates.TemplateResponse(request, "night.html", {})


@app.get("/sample", response_class=HTMLResponse)
async def sample(request: Request):
    """Demo story page — looks at /static/sample/ to decide what to show."""
    sample_dir = WEB_DIR / "static" / "sample"
    has_audio = (sample_dir / "sample.mp3").exists()
    has_video = (sample_dir / "sample.mp4").exists()
    meta = {}
    meta_path = sample_dir / "meta.json"
    if meta_path.exists():
        try:
            import json as _json
            meta = _json.loads(meta_path.read_text())
        except Exception:
            pass
    illustrations = sorted(int(p.stem.split("_")[1]) for p in sample_dir.glob("scene_*.webp")) if sample_dir.exists() else []
    return templates.TemplateResponse(request, "sample.html", {
        "has_media": has_audio,
        "video": has_video,
        "title": meta.get("title"),
        "context": meta.get("context"),
        "duration": int(meta.get("duration", 0)) if meta.get("duration") else None,
        "illustrations": illustrations,
    })


@app.get("/create", response_class=HTMLResponse)
async def create_form(request: Request):
    price = await cfg.get("pricing.story_rub", 999)
    return templates.TemplateResponse(request, "create.html", {"price": price})


@app.post("/create")
async def create_submit(request: Request, topic: str = Form(...), email: str = Form(""),
                        photo: UploadFile = File(None),
                        utm_source: str = Form(""), utm_medium: str = Form(""),
                        utm_campaign: str = Form(""), utm_term: str = Form(""),
                        utm_content: str = Form(""), ref: str = Form(""),
                        website: str = Form("")):
    # Honeypot: humans can't see the field, bots fill anything → silently 200.
    if (website or "").strip():
        logger.info("create honeypot triggered ip=%s", _client_ip(request))
        return RedirectResponse("/", status_code=303)
    topic = (topic or "").strip()[:2000]
    email = (email or "").strip()[:200]
    if len(topic) < 3:
        return RedirectResponse("/create", status_code=303)
    ip = _client_ip(request)
    lifetime_limit = int(await cfg.get("limit.creates_lifetime_per_ip", 5))
    used = await count_orders_by_ip(ip)
    if used >= lifetime_limit:
        logger.info("create blocked (lifetime quota) ip=%s used=%d limit=%d", ip, used, lifetime_limit)
        return _page("Лимит исчерпан",
            f"<h1>Бесплатных сказок больше нет</h1>"
            f"<p>С этого устройства уже создано {used} сказок — это бесплатный лимит. "
            f"Если хотите продолжить — <a href='/feedback'>напишите нам</a>.</p>"
            f"<p><a href='/'>На главную</a></p>")
    # Short-window flood guard (in-memory) — стоп-кран на спам в одну секунду
    if not await _check_rate_limit(ip, 5, window=60):
        logger.info("create rate-limited (60s burst) for ip=%s", ip)
        return _page("Слишком часто",
            f"<h1>Слишком много запросов подряд</h1>"
            f"<p>Подождите минуту и попробуйте снова.</p>"
            f"<p><a href='/'>На главную</a></p>")
    photo_path = None
    if photo is not None and photo.filename:
        data = await photo.read()
        if len(data) <= MAX_PHOTO and (photo.content_type or "").startswith("image/"):
            photo_path = str(UPLOAD_DIR / f"{uuid.uuid4().hex}.jpg")
            Path(photo_path).write_bytes(data)
    # UTM/referrer attribution — passed from client via hidden form fields
    utm_data = {k: (v or "").strip()[:200] or None for k, v in
                (("source", utm_source), ("medium", utm_medium),
                 ("campaign", utm_campaign), ("term", utm_term),
                 ("content", utm_content))}
    oid = await create_order(topic, photo_path, email or None,
                             utm=utm_data, referrer=(ref or "").strip()[:500] or None,
                             ip=ip)
    asyncio.create_task(_compose(oid, topic))
    return RedirectResponse(f"/order/{oid}", status_code=303)


@app.post("/transcribe")
async def transcribe(audio: UploadFile = File(...)):
    data = await audio.read()
    if not data or len(data) > 25 * 1024 * 1024:
        return JSONResponse({"ok": False, "error": "bad audio"}, status_code=400)
    try:
        from engine.transcribe import transcribe_voice
        text = await transcribe_voice(data)
        return JSONResponse({"ok": True, "text": text})
    except Exception as e:
        logger.error("transcribe failed: %s", e, exc_info=True)
        return JSONResponse({"ok": False, "error": "transcription failed"}, status_code=502)


@app.get("/order/{oid}", response_class=HTMLResponse)
async def order_page(request: Request, oid: str):
    o = await get_order(oid)
    if not o:
        return _page("Заказ не найден", "<h1>Заказ не найден</h1><p><a href='/'>На главную</a></p>")
    price = await cfg.get("pricing.story_rub", 999)
    illus = o.get("illustrations") if isinstance(o.get("illustrations"), list) else None
    og_image = (illus[0] if illus else "/static/og_card.jpg")
    if og_image.startswith("/"):
        og_image = PUBLIC_BASE + og_image
    return templates.TemplateResponse(request, "order.html", {
        "oid": oid, "price": price,
        "story_title": o.get("title") or "Персональная сказка",
        "og_image": og_image,
    })


@app.get("/order/{oid}/status")
async def order_status(oid: str):
    o = await get_order(oid)
    if not o:
        return JSONResponse({"status": "not_found"}, status_code=404)
    # Return-from-payment confirmation: if awaiting payment, re-check with YuKassa.
    # This makes the flow work even without the dashboard webhook configured.
    if o["status"] == "awaiting_payment" and o.get("payment_id"):
        try:
            payment = await yookassa_client.get_payment(o["payment_id"])
            if payment.get("status") == "succeeded":
                if await claim_for_generation(oid, o["payment_id"]):
                    asyncio.create_task(_generate(oid))
                o = await get_order(oid)
        except Exception as e:
            logger.warning("order %s payment recheck failed: %s", oid, e)
    return JSONResponse({
        "status": o["status"], "title": o.get("title"),
        "text_html": format_story_html(o.get("story_text")),
        "progress": o.get("progress"),
        "video_url": o.get("video_url"), "audio_url": o.get("audio_url"),
        "email": o.get("email"), "error": o.get("error"),
        "paid": bool(o.get("paid_at")),
        "photo_path": bool(o.get("photo_path")),
        "child_surname": o.get("child_surname"),
        "rating": o.get("rating"),
        "rating_comment": o.get("rating_comment"),
        "rated_at": o.get("rated_at").isoformat() if o.get("rated_at") else None,
    })


async def _revise(oid: str, comment: str):
    """Re-write the existing draft using revise_story_text (keeps continuity)."""
    o = await get_order(oid)
    if not o:
        return
    try:
        from engine.llm_client import revise_story_text
        res = None
        last_err = None
        for attempt in (1, 2):
            try:
                res = await revise_story_text(
                    prev_title=o.get("title") or "",
                    prev_text=o.get("story_text") or "",
                    instruction=comment,
                    original_context=o.get("topic") or "",
                )
                break
            except Exception as e:
                last_err = e
                logger.warning("order %s revise attempt %d failed: %s", oid, attempt, e)
                if attempt == 1:
                    await asyncio.sleep(2)
        if res is None:
            raise last_err
        await update_order(oid, status="text_ready", title=res["title"], story_text=res["text"])
        logger.info("order %s: revised text ready (%s)", oid, res["title"])
    except Exception as e:
        logger.error("order %s revise failed: %s", oid, e, exc_info=True)
        await update_order(oid, status="failed", error=str(e)[:500])


@app.post("/order/{oid}/edit")
async def order_edit(oid: str, comment: str = Form(...)):
    o = await get_order(oid)
    if not o or o["status"] not in ("text_ready", "failed"):
        return JSONResponse({"ok": False, "error": "bad state"}, status_code=400)
    comment = (comment or "").strip()[:1000]
    if not comment:
        return JSONResponse({"ok": False, "error": "empty"}, status_code=400)
    await update_order(oid, status="composing", progress=None, error=None)
    asyncio.create_task(_revise(oid, comment))
    return JSONResponse({"ok": True})


@app.post("/order/{oid}/rate")
async def order_rate(oid: str, rating: int = Form(...), comment: str = Form("")):
    """Post-story feedback. 1-2★ triggers Telegram admin alert."""
    o = await get_order(oid)
    if not o:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    if not (1 <= rating <= 5):
        return JSONResponse({"ok": False, "error": "bad rating"}, status_code=400)
    comment = (comment or "").strip()[:2000] or None
    await set_order_rating(oid, rating, comment)
    title = o.get("title") or "сказка"
    if rating <= 2:
        stars = "★" * rating + "☆" * (5 - rating)
        body = (f"🚨 ПЛОХАЯ ОЦЕНКА {stars} ({rating}★)\n"
                f"«{title}»\n"
                f"{PUBLIC_BASE}/order/{oid}")
        if comment:
            body += f"\n\nКомментарий: «{comment[:500]}»"
        if o.get("email"):
            body += f"\nEmail: {o['email']}"
        try:
            await notify_admin(body)
        except Exception as e:
            logger.warning("rate notify_admin failed: %s", e)
    logger.info("order %s rated %d★ (comment=%s chars)", oid, rating, len(comment or ""))
    return JSONResponse({"ok": True, "rating": rating})


@app.post("/order/{oid}/retry")
async def order_retry(oid: str):
    """Re-run generation for a paid order that failed — no second payment."""
    o = await get_order(oid)
    if not o or o["status"] != "failed" or not o.get("paid_at"):
        return JSONResponse({"ok": False, "error": "bad state"}, status_code=400)
    await update_order(oid, status="generating", error=None, progress=None)
    asyncio.create_task(_generate(oid))
    return JSONResponse({"ok": True})


@app.post("/order/{oid}/recompose")
async def order_recompose(oid: str):
    """Re-run text composition for a failed (unpaid) order — same topic, no data loss."""
    o = await get_order(oid)
    if not o or o["status"] != "failed" or o.get("paid_at"):
        return JSONResponse({"ok": False, "error": "bad state"}, status_code=400)
    topic = (o.get("topic") or "").strip()
    if not topic:
        return JSONResponse({"ok": False, "error": "no topic"}, status_code=400)
    await update_order(oid, status="composing", error=None, progress=None)
    asyncio.create_task(_compose(oid, topic))
    return JSONResponse({"ok": True})


@app.post("/order/{oid}/email")
async def order_set_email(oid: str, email: str = Form(...)):
    """Late email capture — when user submitted /create without email and now
    wants to save the link / receive abandoned-cart nudges."""
    o = await get_order(oid)
    if not o:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    if o.get("email"):
        return JSONResponse({"ok": True, "already": True})
    email = (email or "").strip()[:200]
    if "@" not in email or "." not in email or len(email) < 5:
        return JSONResponse({"ok": False, "error": "bad email"}, status_code=400)
    await update_order(oid, email=email)
    # Fire the instant invite they would have got at text_ready time
    try:
        from web.mailer import send_text_ready_invite
        await send_text_ready_invite(email, o.get("title") or "Сказка", f"{PUBLIC_BASE}/order/{oid}")
    except Exception as e:
        logger.warning("late email invite for %s failed: %s", oid, e)
    return JSONResponse({"ok": True})


@app.post("/order/{oid}/surname")
async def order_set_surname(oid: str, surname: str = Form(...)):
    o = await get_order(oid)
    if not o:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    if o.get("status") not in ("text_ready", "awaiting_payment", "failed"):
        return JSONResponse({"ok": False, "error": "bad state"}, status_code=400)
    s = (surname or "").strip()[:60]
    await update_order(oid, child_surname=s or None)
    return JSONResponse({"ok": True, "surname": s})


@app.post("/order/{oid}/photo")
async def order_set_photo(oid: str, photo: UploadFile = File(...)):
    """Late photo upload — user picks photo on /order text_ready before paying,
    so the illustrations can match their child."""
    o = await get_order(oid)
    if not o:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    if o.get("status") not in ("text_ready", "awaiting_payment", "failed"):
        return JSONResponse({"ok": False, "error": "bad state"}, status_code=400)
    data = await photo.read()
    if not data or len(data) > MAX_PHOTO:
        return JSONResponse({"ok": False, "error": "too big or empty"}, status_code=400)
    if not (photo.content_type or "").startswith("image/"):
        return JSONResponse({"ok": False, "error": "not an image"}, status_code=400)
    photo_path = str(UPLOAD_DIR / f"{uuid.uuid4().hex}.jpg")
    Path(photo_path).write_bytes(data)
    await update_order(oid, photo_path=photo_path)
    return JSONResponse({"ok": True})


@app.post("/order/{oid}/pay")
async def order_pay(oid: str):
    o = await get_order(oid)
    if not o or o["status"] not in ("text_ready", "awaiting_payment", "failed"):
        return JSONResponse({"ok": False, "error": "bad state"}, status_code=400)
    price = await cfg.get("pricing.story_rub", 999)
    email = (o.get("email") or "").strip()
    if not email:
        return JSONResponse({"ok": False, "error": "нужен email для чека"}, status_code=400)
    vat = int(await cfg.get("yukassa.vat_code", 1))
    receipt = {
        "customer": {"email": email},
        "items": [{
            "description": (f"Сказка «{o.get('title') or 'персональная'}»")[:128],
            "quantity": "1.00",
            "amount": {"value": f"{float(price):.2f}", "currency": "RUB"},
            "vat_code": vat,
            "payment_mode": "full_payment",
            "payment_subject": "service",
        }],
    }
    try:
        payment = await yookassa_client.create_payment(
            amount_rub=price,
            description=f"Сказка: {o.get('title') or 'персональная сказка'}",
            return_url=f"{PUBLIC_BASE}/order/{oid}",
            metadata={"order_id": oid}, receipt=receipt)
        url = (payment.get("confirmation") or {}).get("confirmation_url")
        if not url:
            logger.error("order %s: no confirmation_url: %s", oid, payment)
            return JSONResponse({"ok": False, "error": "payment init failed"}, status_code=502)
        await update_order(oid, status="awaiting_payment", payment_id=payment.get("id"))
        try:
            asyncio.create_task(notify_admin(
                f"💳 Юзер нажал «Оплатить» (заказ {oid})\n"
                f"«{o.get('title') or '—'}»\nemail: {o.get('email') or '—'}\n"
                f"{PUBLIC_BASE}/order/{oid}"))
        except Exception:
            pass
        return JSONResponse({"ok": True, "confirmation_url": url})
    except Exception as e:
        logger.error("order %s pay failed: %s", oid, e, exc_info=True)
        return JSONResponse({"ok": False, "error": "payment error"}, status_code=502)


@app.post("/yookassa/webhook")
async def yookassa_webhook(request: Request):
    # YuKassa doesn't sign webhooks → re-fetch the payment to verify before acting.
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": True})
    pid = ((body or {}).get("object") or {}).get("id")
    if not pid:
        return JSONResponse({"ok": True})
    try:
        payment = await yookassa_client.get_payment(pid)
    except Exception as e:
        logger.error("webhook: get_payment %s failed: %s", pid, e)
        return JSONResponse({"ok": True})
    if payment.get("status") != "succeeded":
        return JSONResponse({"ok": True})
    oid = (payment.get("metadata") or {}).get("order_id")
    if oid and await claim_for_generation(oid, pid):
        asyncio.create_task(_generate(oid))
        logger.info("order %s: payment %s succeeded → generating (webhook)", oid, pid)
    return JSONResponse({"ok": True})


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><html lang=ru><head><meta charset=utf-8>"
        f"<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{title} — Сказик</title><style>body{{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;"
        f"max-width:720px;margin:0 auto;padding:28px 20px;color:#2b2350;line-height:1.6}}a{{color:#7c5cff}}</style>"
        f"{METRIKA}</head><body><p><a href='/'>← На главную</a></p>{body}</body></html>")


YANDEX_CAPTCHA_CLIENT_KEY = os.environ.get("YANDEX_CAPTCHA_CLIENT_KEY", "").strip()
YANDEX_CAPTCHA_SERVER_KEY = os.environ.get("YANDEX_CAPTCHA_SERVER_KEY", "").strip()
YANDEX_CAPTCHA_VALIDATE_URL = "https://smartcaptcha.yandexcloud.net/validate"


async def _check_smartcaptcha(token: str, user_ip: str) -> bool:
    """Validate Yandex SmartCaptcha token. Returns False on invalid; True if
    captcha is not configured (graceful fallback so feature can be toggled via env).
    """
    if not YANDEX_CAPTCHA_SERVER_KEY:
        return True  # captcha disabled
    if not token:
        return False
    try:
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(YANDEX_CAPTCHA_VALIDATE_URL,
                              data={"secret": YANDEX_CAPTCHA_SERVER_KEY,
                                    "token": token, "ip": user_ip}) as r:
                data = await r.json(content_type=None)
                ok = (data.get("status") == "ok")
                if not ok:
                    logger.info("smartcaptcha rejected token: %s", data)
                return ok
    except Exception as e:
        logger.warning("smartcaptcha validation error: %s", e)
        # Fail-open: provider down is worse than letting a request through
        return True


@app.get("/feedback", response_class=HTMLResponse)
async def feedback_form(request: Request):
    return templates.TemplateResponse(request, "feedback.html",
                                      {"sent": False,
                                       "captcha_client_key": YANDEX_CAPTCHA_CLIENT_KEY})


@app.post("/feedback", response_class=HTMLResponse)
async def feedback_submit(request: Request, name: str = Form(""), email: str = Form(""),
                          message: str = Form(...), website: str = Form(""),
                          smart_token: str = Form("", alias="smart-token")):
    if (website or "").strip():
        logger.info("feedback honeypot triggered ip=%s", _client_ip(request))
        return templates.TemplateResponse(request, "feedback.html",
                                          {"sent": True,
                                           "captcha_client_key": YANDEX_CAPTCHA_CLIENT_KEY})
    name = (name or "").strip()[:200]
    email = (email or "").strip()[:200]
    message = (message or "").strip()[:4000]
    if len(message) < 3:
        return templates.TemplateResponse(request, "feedback.html",
                                          {"sent": False, "error": "Напишите сообщение.",
                                           "captcha_client_key": YANDEX_CAPTCHA_CLIENT_KEY})
    if YANDEX_CAPTCHA_CLIENT_KEY:
        ok = await _check_smartcaptcha(smart_token, _client_ip(request))
        if not ok:
            logger.info("feedback captcha failed ip=%s", _client_ip(request))
            return templates.TemplateResponse(request, "feedback.html",
                                              {"sent": False,
                                               "error": "Подтвердите, что вы не робот.",
                                               "captcha_client_key": YANDEX_CAPTCHA_CLIENT_KEY})
    fid = await create_feedback(name or None, email or None, message)
    await notify_admin(
        f"💬 Новая обратная связь #{fid}\n"
        f"Имя: {name or '—'}\nEmail: {email or '—'}\n\n{message[:1000]}")
    if email and "@" in email:
        from web.mailer import send_feedback_ack
        asyncio.create_task(send_feedback_ack(email, name, message))
    return templates.TemplateResponse(request, "feedback.html",
                                      {"sent": True,
                                       "captcha_client_key": YANDEX_CAPTCHA_CLIENT_KEY})


async def _legal_ctx(title: str, body_tpl: str) -> dict:
    from web import legal_content as lc
    email = (await cfg.get("legal.email", "") or "").strip()
    address = (await cfg.get("legal.address", "") or "").strip()
    body = body_tpl.format(company=lc.COMPANY, service=lc.SERVICE, site=lc.SITE)
    return {"title": title, "body": body, "edition": lc.EDITION_DATE,
            "company": lc.COMPANY, "inn": lc.INN, "kpp": lc.KPP, "ogrn": lc.OGRN,
            "email": email, "address": address}


@app.get("/oferta", response_class=HTMLResponse)
async def oferta(request: Request):
    from web import legal_content as lc
    ctx = await _legal_ctx("Публичная оферта", lc.OFERTA_BODY)
    ctx["canonical"] = f"{PUBLIC_BASE}/oferta"
    return templates.TemplateResponse(request, "legal.html", ctx)


@app.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request):
    from web import legal_content as lc
    ctx = await _legal_ctx("Политика конфиденциальности", lc.PRIVACY_BODY)
    ctx["canonical"] = f"{PUBLIC_BASE}/privacy"
    return templates.TemplateResponse(request, "legal.html", ctx)


@app.get("/admin/checkpoint")
async def admin_checkpoint(token: str = "", hours: int = 48):
    """Machine-readable funnel snapshot for cloud routines.
    Returns JSON with: web/bot funnel, UTM ROI, revenue, email queue, errors,
    first-ad-payment status, Direct spend, and rule-based recommendations.

    Auth: ?token=$ADMIN_TOKEN
    Example: GET /admin/checkpoint?token=...&hours=48
    """
    import datetime as _dt
    expected = os.environ.get("ADMIN_TOKEN", "")
    if not expected or token != expected:
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403,
                            headers={"X-Robots-Tag": "noindex, nofollow, noarchive"})
    hours = max(1, min(int(hours), 24 * 30))
    excluded = [e.strip().lower() for e in
                os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()]
    interval = f"{hours} hours"
    price = int(await cfg.get("pricing.story_rub", 999))

    async with db_mod._pool.acquire() as c:
        # WEB funnel — real customers only (exclude admin/test emails)
        web_funnel = await c.fetchrow(f"""
            SELECT
              COUNT(*)                                            AS total,
              COUNT(*) FILTER (WHERE status='composing')          AS composing,
              COUNT(*) FILTER (WHERE status='text_ready')         AS text_ready,
              COUNT(*) FILTER (WHERE status='awaiting_payment')   AS awaiting_payment,
              COUNT(*) FILTER (WHERE status='generating')         AS generating,
              COUNT(*) FILTER (WHERE status='done')               AS done,
              COUNT(*) FILTER (WHERE status='failed')             AS failed,
              COUNT(*) FILTER (WHERE status='abandoned')          AS abandoned,
              COUNT(*) FILTER (WHERE paid_at IS NOT NULL)         AS paid,
              COUNT(*) FILTER (WHERE utm_source IS NOT NULL)      AS attributed,
              COUNT(*) FILTER (WHERE utm_source IS NOT NULL AND paid_at IS NOT NULL) AS ad_paid,
              COUNT(DISTINCT email) FILTER (WHERE email IS NOT NULL AND email<>'') AS unique_emails
            FROM web_orders
            WHERE created_at > NOW() - INTERVAL '{interval}'
              AND NOT (LOWER(COALESCE(email,'')) = ANY($1::text[]))
        """, excluded)
        # First ad-paid order (if any) — the goal hook trigger
        first_ad_paid = await c.fetchrow("""
            SELECT id, paid_at, utm_source, utm_campaign, utm_content, email, title
            FROM web_orders
            WHERE paid_at IS NOT NULL AND utm_source IS NOT NULL
              AND NOT (LOWER(COALESCE(email,'')) = ANY($1::text[]))
            ORDER BY paid_at ASC LIMIT 1
        """, excluded)
        # UTM breakdown for period
        utm_rows = await c.fetch(f"""
            SELECT
              COALESCE(utm_source,'(direct)') AS src,
              COALESCE(utm_campaign,'—')      AS camp,
              COALESCE(utm_content,'—')       AS content,
              COUNT(*)                        AS visits,
              COUNT(*) FILTER (WHERE status='text_ready' OR paid_at IS NOT NULL) AS leads,
              COUNT(*) FILTER (WHERE paid_at IS NOT NULL) AS paid
            FROM web_orders
            WHERE created_at > NOW() - INTERVAL '{interval}'
              AND NOT (LOWER(COALESCE(email,'')) = ANY($1::text[]))
            GROUP BY src, camp, content
            ORDER BY paid DESC, visits DESC
            LIMIT 30
        """, excluded)
        # Recent fresh leads (text_ready, last `interval`) — useful for routine to spot warming leads
        recent_leads = await c.fetch(f"""
            SELECT id, created_at, status, COALESCE(email,'') AS email,
                   COALESCE(utm_content,'') AS utm_content,
                   ROUND(EXTRACT(EPOCH FROM (NOW() - created_at))/60::numeric, 1) AS age_min
            FROM web_orders
            WHERE created_at > NOW() - INTERVAL '{interval}'
              AND status='text_ready'
              AND NOT (LOWER(COALESCE(email,'')) = ANY($1::text[]))
            ORDER BY created_at DESC LIMIT 20
        """, excluded)
        # BOT funnel
        bot_funnel = await c.fetchrow(f"""
            SELECT
              COUNT(*)                                       AS total,
              COUNT(*) FILTER (WHERE status='completed')     AS completed,
              COUNT(*) FILTER (WHERE status='generating')    AS generating,
              COUNT(*) FILTER (WHERE status='failed')        AS failed
            FROM stories
            WHERE created_at > NOW() - INTERVAL '{interval}'
        """)
        # Email queue health
        email_q = await c.fetchrow("""
            SELECT
              COUNT(*) FILTER (WHERE status='queued')  AS queued,
              COUNT(*) FILTER (WHERE status='sent')    AS sent,
              COUNT(*) FILTER (WHERE status='failed')  AS failed,
              COUNT(*) FILTER (WHERE status='queued' AND next_try_at < NOW() - INTERVAL '5 minutes') AS overdue,
              MAX(sent_at)                             AS last_sent
            FROM email_outbox
            WHERE created_at > NOW() - INTERVAL '7 days'
        """)
        # Errors last `hours`
        try:
            err_count = await c.fetchval(
                f"SELECT COUNT(*) FROM errors WHERE created_at > NOW() - INTERVAL '{interval}'") or 0
            recent_errors = await c.fetch(f"""
                SELECT id, created_at, LEFT(error_message, 200) AS message
                FROM errors WHERE created_at > NOW() - INTERVAL '{interval}'
                ORDER BY created_at DESC LIMIT 5
            """)
        except Exception:
            err_count = 0
            recent_errors = []
        # Feedback
        try:
            fb_new = await c.fetchval(
                "SELECT COUNT(*) FROM feedback WHERE status='new'") or 0
        except Exception:
            fb_new = 0

    # Direct API spend today + clicks today (best-effort)
    direct = {"available": False}
    direct_token = os.environ.get("YANDEX_DIRECT_TOKEN", "")
    if direct_token:
        try:
            import aiohttp
            today = _dt.datetime.utcnow().date().isoformat()
            body = {"params": {
                "SelectionCriteria": {"DateFrom": today, "DateTo": today,
                                      "Filter": [{"Field": "CampaignId", "Operator": "IN",
                                                  "Values": ["710328773", "710328840"]}]},
                "FieldNames": ["CampaignName", "Clicks", "Cost", "Impressions"],
                "ReportName": f"ck_{int(_dt.datetime.utcnow().timestamp())}",
                "ReportType": "CAMPAIGN_PERFORMANCE_REPORT",
                "DateRangeType": "CUSTOM_DATE", "Format": "TSV", "IncludeVAT": "YES"}}
            headers = {"Authorization": f"Bearer {direct_token}",
                       "Accept-Language": "ru", "Content-Type": "application/json",
                       "processingMode": "auto", "returnMoneyInMicros": "false",
                       "skipReportHeader": "true", "skipReportSummary": "true"}
            async with aiohttp.ClientSession() as sess:
                async with sess.post("https://api.direct.yandex.com/json/v5/reports",
                                     json=body, headers=headers, timeout=30) as resp:
                    text = await resp.text()
                    if resp.status == 200:
                        lines = [l.split("\t") for l in text.strip().split("\n")[1:] if l.strip()]
                        direct = {
                            "available": True,
                            "date": today,
                            "campaigns": [
                                {"name": l[0], "clicks": int(l[1]), "cost_rub": float(l[2]),
                                 "impressions": int(l[3])}
                                for l in lines if len(l) >= 4
                            ],
                        }
                        direct["total_clicks"] = sum(c["clicks"] for c in direct["campaigns"])
                        direct["total_cost_rub"] = round(sum(c["cost_rub"] for c in direct["campaigns"]), 2)
                    else:
                        direct = {"available": False, "status": resp.status, "snippet": text[:200]}
        except Exception as e:
            direct = {"available": False, "error": str(e)[:200]}

    # Recommendations — rule-based decisions for the routine
    recs = []
    ad_paid = int(web_funnel["ad_paid"] or 0) + 0  # only web paid (bot has no UTM)
    attributed = int(web_funnel["attributed"] or 0)
    if ad_paid == 0 and attributed >= 200:
        recs.append({
            "level": "alert",
            "code": "no_ad_paid_high_traffic",
            "msg": f"0 ad-payments at {attributed} attributed visits — problem is NOT traffic. "
                   "Review: pricing, landing UTP, photo upload friction, payment friction."
        })
    elif ad_paid == 0 and attributed >= 50:
        recs.append({
            "level": "warn",
            "code": "no_ad_paid_medium_traffic",
            "msg": f"0 ad-payments at {attributed} attributed visits — too early to decide. "
                   "Wait for ≥150 attributed visits."
        })
    elif ad_paid >= 1 and ad_paid < 10:
        recs.append({
            "level": "info",
            "code": "ad_paid_collecting",
            "msg": f"{ad_paid} ad-payments collected. Need ≥10 for PAY_FOR_CONVERSIONS strategy."
        })
    elif ad_paid >= 10:
        recs.append({
            "level": "success",
            "code": "ad_paid_cpa_ready",
            "msg": f"{ad_paid} ad-payments — READY for PAY_FOR_CONVERSIONS migration. "
                   "Switch Search campaign to CPA strategy with target CPA = 0.7 * price."
        })
    if err_count > 0:
        recs.append({"level": "warn", "code": "recent_errors",
                     "msg": f"{err_count} errors in last {hours}h — review logs."})
    if fb_new > 0:
        recs.append({"level": "info", "code": "new_feedback",
                     "msg": f"{fb_new} new feedback entries — read & reply."})
    if email_q and email_q["overdue"] > 0:
        recs.append({"level": "warn", "code": "email_overdue",
                     "msg": f"{email_q['overdue']} emails stuck in queue >5 min — check SMTP."})
    if direct.get("available") and direct.get("total_clicks", 0) < 5 and not first_ad_paid:
        recs.append({"level": "info", "code": "low_direct_traffic",
                     "msg": f"Only {direct.get('total_clicks')} Direct clicks today — keywords still calibrating "
                            "or moderation pending. Wait 1-3 days, then consider broadening."})

    from fastapi.encoders import jsonable_encoder
    return JSONResponse(jsonable_encoder({
        "ok": True,
        "as_of_utc": _dt.datetime.utcnow().isoformat() + "Z",
        "period_hours": hours,
        "price_rub": price,
        "goal_first_ad_payment_reached": bool(first_ad_paid),
        "first_ad_payment": (None if not first_ad_paid else {
            "order_id": first_ad_paid["id"],
            "paid_at": first_ad_paid["paid_at"].isoformat() if first_ad_paid["paid_at"] else None,
            "utm_source": first_ad_paid["utm_source"],
            "utm_campaign": first_ad_paid["utm_campaign"],
            "utm_content": first_ad_paid["utm_content"],
            "email": first_ad_paid["email"],
            "title": first_ad_paid["title"],
        }),
        "funnel": {
            "web": dict(web_funnel),
            "bot": dict(bot_funnel),
        },
        "by_utm": [dict(r) for r in utm_rows],
        "recent_text_ready_leads": [{
            "id": r["id"], "email": r["email"], "utm_content": r["utm_content"],
            "age_min": float(r["age_min"]),
            "created_at": r["created_at"].isoformat()
        } for r in recent_leads],
        "revenue_rub": int(web_funnel["paid"] or 0) * price,
        "email_queue": dict(email_q) if email_q else {},
        "errors": {
            "count": err_count,
            "recent": [{"id": r["id"], "at": r["created_at"].isoformat(), "message": r["message"]}
                       for r in recent_errors],
        },
        "feedback_new": fb_new,
        "direct": direct,
        "recommendations": recs,
    }), headers={"X-Robots-Tag": "noindex, nofollow, noarchive"})


@app.get("/admin/stats", response_class=HTMLResponse)
async def admin_stats(request: Request, token: str = ""):
    """Lightweight founder dashboard: orders + conversion + UTM ROI.
    Excludes orders from founder/test emails so KPIs reflect real customers only."""
    expected = os.environ.get("ADMIN_TOKEN", "")
    noindex_headers = {"X-Robots-Tag": "noindex, nofollow, noarchive"}
    if not expected or token != expected:
        return HTMLResponse("forbidden", status_code=403, headers=noindex_headers)
    # Founder + test emails to exclude from counts (real-customer view)
    excluded = [e.strip().lower() for e in
                os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()]
    async with db_mod._pool.acquire() as c:
        funnel = await c.fetchrow("""
            SELECT
              COUNT(*)                                           AS total,
              COUNT(*) FILTER (WHERE status='text_ready')        AS text_ready,
              COUNT(*) FILTER (WHERE status='generating')        AS generating,
              COUNT(*) FILTER (WHERE status='done')              AS done,
              COUNT(*) FILTER (WHERE status='failed')            AS failed,
              COUNT(*) FILTER (WHERE paid_at IS NOT NULL)        AS paid,
              COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '24 hours') AS last_24h,
              COUNT(*) FILTER (WHERE paid_at > NOW() - INTERVAL '24 hours')    AS paid_24h
            FROM web_orders
            WHERE NOT (LOWER(COALESCE(email,'')) = ANY($1::text[]))
        """, excluded)
        utm = await c.fetch("""
            SELECT
              COALESCE(utm_source, '(direct)')        AS src,
              COALESCE(utm_campaign, '—')             AS camp,
              COUNT(*)                                AS visits,
              COUNT(*) FILTER (WHERE status='text_ready' OR paid_at IS NOT NULL) AS lead,
              COUNT(*) FILTER (WHERE paid_at IS NOT NULL) AS paid
            FROM web_orders
            WHERE created_at > NOW() - INTERVAL '30 days'
              AND NOT (LOWER(COALESCE(email,'')) = ANY($1::text[]))
            GROUP BY src, camp
            ORDER BY paid DESC, visits DESC
            LIMIT 30
        """, excluded)
        recent = await c.fetch("""
            SELECT id, created_at, status, title, COALESCE(email,'—') AS email,
                   paid_at IS NOT NULL AS paid
            FROM web_orders
            ORDER BY created_at DESC
            LIMIT 20
        """)
        # Founder/test orders count — shown separately so we know they exist
        excl_count = await c.fetchval(
            "SELECT COUNT(*) FROM web_orders WHERE LOWER(COALESCE(email,'')) = ANY($1::text[])",
            excluded) or 0
        fb_count = await c.fetchval("SELECT COUNT(*) FROM feedback WHERE status='new'")
        # Email queue health
        try:
            email_stats = await c.fetchrow("""
                SELECT
                  COUNT(*) FILTER (WHERE status='queued')  AS queued,
                  COUNT(*) FILTER (WHERE status='sent')    AS sent,
                  COUNT(*) FILTER (WHERE status='failed')  AS failed,
                  COUNT(*) FILTER (WHERE status='queued' AND next_try_at < NOW() - INTERVAL '5 minutes') AS overdue,
                  MAX(sent_at)                             AS last_sent
                FROM email_outbox
            """)
        except Exception:
            email_stats = {"queued": 0, "sent": 0, "failed": 0, "overdue": 0, "last_sent": None}
        # Recent errors (last 24h)
        try:
            err_count = await c.fetchval(
                "SELECT COUNT(*) FROM errors WHERE created_at > NOW() - INTERVAL '24 hours'") or 0
        except Exception:
            err_count = 0
        # Total revenue (paid orders × current price)
        price = await cfg.get("pricing.story_rub", 999)
        revenue = int(funnel["paid"] or 0) * int(price)
    rows_utm = "\n".join(
        f"<tr><td>{r['src']}</td><td>{r['camp']}</td>"
        f"<td>{r['visits']}</td><td>{r['lead']}</td><td>{r['paid']}</td>"
        f"<td>{(r['paid']*100//r['visits']) if r['visits'] else 0}%</td></tr>"
        for r in utm)
    rows_recent = "\n".join(
        f"<tr><td><a href='/admin/order/{r['id']}?token={token}' style='color:#7c5cff;font-weight:600;text-decoration:none'>{r['id'][:12]}…</a></td>"
        f"<td>{r['created_at'].strftime('%Y-%m-%d %H:%M')}</td>"
        f"<td>{r['status']}{'  💜' if r['paid'] else ''}</td>"
        f"<td>{(r['title'] or '—')[:40]}</td><td>{r['email']}</td></tr>"
        for r in recent)
    cr = (funnel['paid']*100//funnel['total']) if funnel['total'] else 0
    excl_note = (f"<div style='color:#6b6390;font-size:13px;margin:6px 0 14px'>"
                 f"⚙️ Исключено из счёта: <b>{excl_count}</b> заказ(ов) от {len(excluded)} email-ов "
                 f"(основатель/тесты, через .env ADMIN_EMAILS)</div>") if excl_count else ""
    return HTMLResponse(headers=noindex_headers, content=
        f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Admin · Stats</title>
<meta name="robots" content="noindex, nofollow, noarchive">
<style>body{{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;max-width:1100px;margin:0 auto;padding:20px;color:#2b2350;background:#fbf7ff}}
h1{{margin:0 0 14px}}h2{{margin:24px 0 10px;font-size:18px}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px}}
.kpi{{background:#fff;border-radius:12px;padding:14px;box-shadow:0 4px 12px rgba(0,0,0,.04)}}
.kpi b{{display:block;font-size:24px}}.kpi span{{color:#6b6390;font-size:13px}}
table{{width:100%;border-collapse:collapse;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 4px 12px rgba(0,0,0,.04)}}
th,td{{padding:8px 12px;text-align:left;font-size:14px;border-bottom:1px solid #ece6fb}}
th{{background:#f1ebff;color:#2b2350;font-weight:700}}
tr:last-child td{{border-bottom:0}}
code{{font-family:ui-monospace,monospace;font-size:12px}}
</style></head><body>
<h1>📊 Skazik · admin</h1>
{excl_note}
<div class="kpis">
  <div class="kpi"><b>{funnel['total']}</b><span>Всего заказов</span></div>
  <div class="kpi"><b>{funnel['paid']}</b><span>Оплачено · CR {cr}%</span></div>
  <div class="kpi"><b>{funnel['done']}</b><span>Сказок собрано</span></div>
  <div class="kpi"><b>{funnel['failed']}</b><span>Failed</span></div>
  <div class="kpi"><b>{funnel['last_24h']}</b><span>За 24ч</span></div>
  <div class="kpi"><b>{funnel['paid_24h']}</b><span>Оплат за 24ч</span></div>
  <div class="kpi"><b>{revenue:,}₽</b><span>Выручка (всего)</span></div>
  <div class="kpi"><b>{fb_count}</b><span>Новых отзывов</span></div>
</div>

<h2>Email queue · Errors</h2>
<div class="kpis">
  <div class="kpi"><b>{email_stats['queued']}</b><span>В очереди{' ⚠️' if email_stats['overdue'] else ''}</span></div>
  <div class="kpi"><b>{email_stats['sent']}</b><span>Отправлено</span></div>
  <div class="kpi"><b>{email_stats['failed']}</b><span>Failed</span></div>
  <div class="kpi"><b>{(email_stats['last_sent'].strftime('%H:%M') if email_stats['last_sent'] else '—')}</b><span>Последнее письмо</span></div>
  <div class="kpi"><b>{err_count}</b><span>Ошибок за 24ч</span></div>
</div>
<h2>UTM-кампании (30 дней)</h2>
<table><tr><th>source</th><th>campaign</th><th>visits</th><th>lead</th><th>paid</th><th>CR</th></tr>
{rows_utm or '<tr><td colspan="6">пока нет данных</td></tr>'}</table>
<h2>Последние заказы <span style='color:#6b6390;font-size:13px;font-weight:400'>(клик на id — детали)</span></h2>
<table><tr><th>id</th><th>создан</th><th>status</th><th>title</th><th>email</th></tr>
{rows_recent}</table>
</body></html>""")


def _esc_html(s):
    if s is None:
        return ""
    return (str(s)
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _fmt_ts(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else "—"


def _file_link(p):
    """Convert a local fs path under /app/media/ (or relative web path /media/...)
    to a clickable public HTTP URL. noindex protection via robots.txt + middleware."""
    if not p:
        return None
    s = str(p)
    if s.startswith("http://") or s.startswith("https://"):
        return s
    if s.startswith("/app/media/"):
        return PUBLIC_BASE + "/media" + s[len("/app/media"):]
    if s.startswith("/media/"):
        return PUBLIC_BASE + s
    if s.startswith("media/"):
        return PUBLIC_BASE + "/" + s
    return None


@app.get("/admin/order/{oid}", response_class=HTMLResponse)
async def admin_order_detail(oid: str, token: str = ""):
    """Per-order admin detail: full order fields, pipeline API calls, emails sent."""
    expected = os.environ.get("ADMIN_TOKEN", "")
    noindex_headers = {"X-Robots-Tag": "noindex, nofollow, noarchive"}
    if not expected or token != expected:
        return HTMLResponse("forbidden", status_code=403, headers=noindex_headers)
    async with db_mod._pool.acquire() as c:
        o = await c.fetchrow("SELECT * FROM web_orders WHERE id=$1", oid)
        if not o:
            return HTMLResponse("order not found", status_code=404, headers=noindex_headers)
        # Pipeline API calls: try strict link via stories first; web-flow doesn't write
        # to stories, so fallback to time-window heuristic.
        api_calls = []
        story_row = None
        api_calls_approx = False
        mid = o["media_order_id"]
        if mid:
            story_row = await c.fetchrow("SELECT id, title FROM stories WHERE order_id=$1", mid)
        if story_row:
            api_calls = await c.fetch(
                "SELECT id, created_at, service, model, purpose, status, duration_ms, "
                "tokens_in, tokens_out, cost_usd, error "
                "FROM api_calls WHERE story_id=$1 ORDER BY id", story_row["id"])
        else:
            # Heuristic: web-flow's api_calls have story_id=NULL. Take all such calls
            # in a generous window around the order's lifetime.
            window_end = (o["paid_at"] or o["created_at"]) + timedelta(hours=1)
            api_calls = await c.fetch(
                "SELECT id, created_at, service, model, purpose, status, duration_ms, "
                "tokens_in, tokens_out, cost_usd, error "
                "FROM api_calls WHERE story_id IS NULL "
                "AND created_at BETWEEN $1 AND $2 ORDER BY id",
                o["created_at"], window_end)
            api_calls_approx = True
        # Emails: matched by recipient = order.email + meta order_id OR sent after order created
        emails = []
        if o["email"]:
            emails = await c.fetch(
                "SELECT id, created_at, subject, status, sent_at, retries, last_error, meta, next_try_at "
                "FROM email_outbox WHERE to_addr=$1 AND created_at >= $2 "
                "ORDER BY id", o["email"], o["created_at"])
        # Errors with traceback if logged
        errors = []
        try:
            errors = await c.fetch(
                "SELECT id, created_at, source, error_text FROM errors "
                "WHERE story_id=$1 ORDER BY id DESC LIMIT 10",
                story_row["id"] if story_row else -1)
        except Exception:
            pass
    # Build fields card
    def kv(label, value, mono=False):
        v = _esc_html(value) if value is not None else "<span style='color:#999'>—</span>"
        cls = "code" if mono else ""
        return f"<tr><th>{label}</th><td class='{cls}'>{v}</td></tr>"
    illus = o["illustrations"] or []
    if isinstance(illus, str):
        try:
            illus = json.loads(illus)
        except Exception:
            illus = []
    def _ahref(p):
        url = _file_link(p)
        return f"<a href='{_esc_html(url)}' target='_blank' rel='noindex,nofollow,noreferrer'>{_esc_html(p)}</a>" if url else _esc_html(p) if p else "—"
    illus_html = "<br>".join(_ahref(u) for u in illus) if illus else "—"
    fields_rows = (
        kv("id", o["id"], mono=True)
        + kv("создан", _fmt_ts(o["created_at"]))
        + kv("status", o["status"])
        + kv("title", o["title"])
        + kv("topic", (o["topic"] or "")[:500])
        + f"<tr><th>photo_path</th><td class='code'>{_ahref(o['photo_path']) if o['photo_path'] else '—'}</td></tr>"
        + kv("email", o["email"])
        + kv("child_surname", o["child_surname"])
        + kv("paid_at", _fmt_ts(o["paid_at"]))
        + kv("payment_id", o["payment_id"], mono=True)
        + kv("media_order_id", o["media_order_id"], mono=True)
        + f"<tr><th>video_url</th><td>{_ahref(o['video_url'])}</td></tr>"
        + f"<tr><th>audio_url</th><td>{_ahref(o['audio_url'])}</td></tr>"
        + f"<tr><th>illustrations</th><td>{illus_html}</td></tr>"
        + kv("progress (последний)", o["progress"])
        + kv("error", o["error"])
        + kv("rating", f"{o['rating']}★" if o["rating"] else None)
        + kv("rating_comment", o["rating_comment"])
        + kv("rated_at", _fmt_ts(o["rated_at"]))
        + kv("followup_email_sent_at", _fmt_ts(o["followup_email_sent_at"]))
        + kv("ip", o["ip"], mono=True)
        + kv("UTM source", o["utm_source"])
        + kv("UTM medium", o["utm_medium"])
        + kv("UTM campaign", o["utm_campaign"])
        + kv("UTM term", o["utm_term"])
        + kv("UTM content", o["utm_content"])
        + kv("referrer", o["referrer"])
    )
    # API calls
    if api_calls:
        api_rows = "\n".join(
            f"<tr><td>{_fmt_ts(a['created_at'])}</td>"
            f"<td>{_esc_html(a['purpose'])}</td>"
            f"<td class='code'>{_esc_html(a['model'])}</td>"
            f"<td>{_esc_html(a['status']) or '—'}</td>"
            f"<td>{a['duration_ms'] or '—'}ms</td>"
            f"<td>{a['tokens_in'] or '—'}/{a['tokens_out'] or '—'}</td>"
            f"<td>${(a['cost_usd'] or 0):.4f}</td>"
            f"<td>{_esc_html((a['error'] or '')[:200])}</td></tr>"
            for a in api_calls)
        total_cost = sum((a['cost_usd'] or 0) for a in api_calls)
        approx_note = (
            "<p style='color:#a67d00;font-size:12.5px;margin:0 0 6px'>"
            "⚠ Heuristic match by time-window (web-flow doesn't write to <code>stories</code>). "
            "Возможны посторонние вызовы из параллельных заказов.</p>") if api_calls_approx else ""
        api_html = (
            f"{approx_note}<table><tr><th>время</th><th>purpose</th><th>model</th><th>status</th>"
            f"<th>длит.</th><th>tok in/out</th><th>$</th><th>error</th></tr>"
            f"{api_rows}<tr style='font-weight:700;background:#f1ebff'>"
            f"<td colspan='6'>Итого вызовов: {len(api_calls)}</td>"
            f"<td>${total_cost:.4f}</td><td>≈ {total_cost*100:.1f}₽</td></tr></table>")
    else:
        api_html = "<p style='color:#999'>В этом окне api_calls не найдено.</p>"
    # Emails
    if emails:
        email_rows = []
        for e in emails:
            meta = e["meta"]
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    pass
            meta_txt = json.dumps(meta, ensure_ascii=False) if meta else "—"
            email_rows.append(
                f"<tr><td>{_fmt_ts(e['created_at'])}</td>"
                f"<td>{_esc_html(e['subject'])}</td>"
                f"<td>{_esc_html(e['status'])}</td>"
                f"<td>{_fmt_ts(e['sent_at'])}</td>"
                f"<td>{e['retries'] or 0}</td>"
                f"<td class='code'>{_esc_html(meta_txt)}</td>"
                f"<td>{_esc_html((e['last_error'] or '')[:200])}</td></tr>")
        emails_html = (
            "<table><tr><th>создано</th><th>subject</th><th>status</th>"
            "<th>отправлено</th><th>retries</th><th>meta</th><th>last_error</th></tr>"
            + "\n".join(email_rows) + "</table>")
    else:
        emails_html = "<p style='color:#999'>Писем не было (либо email не указан).</p>"
    # Errors traceback
    if errors:
        err_html = "".join(
            f"<details style='margin:8px 0;background:#fff5f5;border:1px solid #ffcccc;border-radius:8px;padding:10px'>"
            f"<summary style='cursor:pointer;color:#c0392b;font-weight:700'>{_fmt_ts(er['created_at'])} · {_esc_html(er['source'] or '?')}</summary>"
            f"<pre style='white-space:pre-wrap;font-size:12px;margin-top:8px'>{_esc_html(er['error_text'])}</pre>"
            f"</details>" for er in errors)
    else:
        err_html = "<p style='color:#999'>Ошибок не было.</p>"
    # Story text in collapsible
    story_html = ""
    if o["story_text"]:
        story_html = (
            f"<details style='margin-top:12px'><summary style='cursor:pointer;color:#7c5cff;font-weight:700'>"
            f"📖 Текст сказки ({len(o['story_text'])} симв)</summary>"
            f"<div style='background:#fff;padding:14px 18px;border-radius:10px;margin-top:8px;white-space:pre-wrap;line-height:1.6;font-size:14.5px'>"
            f"{_esc_html(o['story_text'])}</div></details>")
    return HTMLResponse(headers=noindex_headers, content=f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Order {oid[:8]} · admin</title>
<meta name="robots" content="noindex, nofollow, noarchive">
<style>body{{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;max-width:1200px;margin:0 auto;padding:20px;color:#2b2350;background:#fbf7ff}}
h1{{margin:0 0 6px;font-size:22px}}h2{{margin:24px 0 10px;font-size:17px}}
a{{color:#7c5cff}}
table{{width:100%;border-collapse:collapse;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 4px 12px rgba(0,0,0,.04);margin-bottom:8px}}
th,td{{padding:8px 12px;text-align:left;font-size:13.5px;border-bottom:1px solid #ece6fb;vertical-align:top}}
th{{background:#f1ebff;color:#2b2350;font-weight:700;white-space:nowrap}}
tr:last-child td{{border-bottom:0}}
.code{{font-family:ui-monospace,SF Mono,Consolas,monospace;font-size:12.5px;color:#3a2f6b;word-break:break-all}}
.fields td{{font-size:13.5px}}
.fields th{{width:200px}}
.back{{display:inline-block;margin-bottom:14px;color:#6b6390;text-decoration:none;font-size:14px}}
.back:hover{{color:#7c5cff}}
</style></head><body>
<a class="back" href="/admin/stats?token={token}">← К списку заказов</a>
<h1>Заказ {oid}</h1>
<p style="color:#6b6390;font-size:13.5px;margin:0 0 18px">
  status: <b>{o['status']}</b>{' · 💜 оплачен' if o['paid_at'] else ''}
  {' · ' + str(o['rating']) + '★' if o['rating'] else ''}
</p>

<h2>Поля заказа</h2>
<table class="fields">{fields_rows}</table>

<h2>Пайплайн (api_calls)</h2>
{api_html}

<h2>Письма (email_outbox)</h2>
{emails_html}

<h2>Ошибки (errors)</h2>
{err_html}

{story_html}

</body></html>""")
