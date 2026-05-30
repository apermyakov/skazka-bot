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
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
from fastapi import FastAPI, Request, Form, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import db.database as db_mod
from db.database import init_db
from db.config_manager import cfg
from web.orders import init_orders, create_order, get_order, update_order, claim_for_generation, create_feedback
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
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    # Email outbox: persistent queue + background worker
    try:
        from web.email_queue import init_email_queue, start_worker
        await init_email_queue()
        start_worker()
    except Exception as e:
        logger.warning("email queue init failed: %s", e)
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


# ── background workers ──
async def _compose(oid: str, topic: str):
    from engine.llm_client import generate_story_text
    res = None
    last_err = None
    for attempt in (1, 2):
        try:
            res = await generate_story_text(topic)
            if attempt > 1:
                logger.info("order %s: text gen succeeded on attempt %d", oid, attempt)
            break
        except Exception as e:
            last_err = e
            logger.warning("order %s compose attempt %d failed: %s", oid, attempt, e)
            if attempt == 1:
                await asyncio.sleep(2)
    if res is None:
        logger.error("order %s compose failed after retries: %s", oid, last_err, exc_info=True)
        await update_order(oid, status="failed", error=str(last_err)[:500])
        await notify_admin(f"⚠️ Текст НЕ сгенерировался (заказ {oid}): {str(last_err)[:200]}")
        return
    await update_order(oid, status="text_ready", title=res["title"], story_text=res["text"])
    logger.info("order %s: text ready (%s)", oid, res["title"])
    o = await get_order(oid)
    await notify_admin(f"📝 Текст сказки готов\n«{res['title']}»\nemail: {(o or {}).get('email') or '—'}\n{PUBLIC_BASE}/order/{oid}")


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
            from web.mailer import send_story_ready
            cover = None
            if illus and isinstance(illus, list) and illus:
                first = illus[0]
                cover = (PUBLIC_BASE + first) if isinstance(first, str) and first.startswith("/") else first
            asyncio.create_task(send_story_ready(
                buyer_email,
                result.get("title") or "Ваша сказка",
                f"{PUBLIC_BASE}/order/{oid}",
                cover_url=cover))
    except Exception as e:
        logger.error("order %s generate failed: %s", oid, e, exc_info=True)
        await update_order(oid, status="failed", error=str(e)[:500])
        await notify_admin(f"🔴 Сказка НЕ собралась после оплаты (заказ {oid}): {str(e)[:200]}")


# ── pages ──
@app.get("/health")
async def health():
    return JSONResponse({"ok": True})


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots():
    return (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /order/\n"
        "Disallow: /yookassa/\n"
        f"Sitemap: {PUBLIC_BASE}/sitemap.xml\n"
    )


@app.get("/sitemap.xml", response_class=Response)
async def sitemap():
    today = datetime.utcnow().strftime("%Y-%m-%d")
    urls = [
        ("/", "1.0", "weekly"),
        ("/create", "0.9", "weekly"),
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
    limit = int(await cfg.get("limit.creates_per_hour_per_ip", 5))
    if not await _check_rate_limit(_client_ip(request), limit):
        logger.info("create rate-limited for ip=%s", _client_ip(request))
        return _page("Слишком часто",
            f"<h1>Слишком много запросов</h1>"
            f"<p>Вы создали более {limit} сказок за последний час. "
            f"Попробуйте чуть позже — это защита от спама.</p>"
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
                             utm=utm_data, referrer=(ref or "").strip()[:500] or None)
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


@app.get("/feedback", response_class=HTMLResponse)
async def feedback_form(request: Request):
    return templates.TemplateResponse(request, "feedback.html", {"sent": False})


@app.post("/feedback", response_class=HTMLResponse)
async def feedback_submit(request: Request, name: str = Form(""), email: str = Form(""),
                          message: str = Form(...), website: str = Form("")):
    if (website or "").strip():
        logger.info("feedback honeypot triggered ip=%s", _client_ip(request))
        return templates.TemplateResponse(request, "feedback.html", {"sent": True})
    name = (name or "").strip()[:200]
    email = (email or "").strip()[:200]
    message = (message or "").strip()[:4000]
    if len(message) < 3:
        return templates.TemplateResponse(request, "feedback.html",
                                          {"sent": False, "error": "Напишите сообщение."})
    fid = await create_feedback(name or None, email or None, message)
    await notify_admin(
        f"💬 Новая обратная связь #{fid}\n"
        f"Имя: {name or '—'}\nEmail: {email or '—'}\n\n{message[:1000]}")
    if email and "@" in email:
        from web.mailer import send_feedback_ack
        asyncio.create_task(send_feedback_ack(email, name, message))
    return templates.TemplateResponse(request, "feedback.html", {"sent": True})


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


@app.get("/admin/stats", response_class=HTMLResponse)
async def admin_stats(request: Request, token: str = ""):
    """Lightweight founder dashboard: orders + conversion + UTM ROI."""
    expected = os.environ.get("ADMIN_TOKEN", "")
    if not expected or token != expected:
        return HTMLResponse("forbidden", status_code=403)
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
        """)
        utm = await c.fetch("""
            SELECT
              COALESCE(utm_source, '(direct)')        AS src,
              COALESCE(utm_campaign, '—')             AS camp,
              COUNT(*)                                AS visits,
              COUNT(*) FILTER (WHERE status='text_ready' OR paid_at IS NOT NULL) AS lead,
              COUNT(*) FILTER (WHERE paid_at IS NOT NULL) AS paid
            FROM web_orders
            WHERE created_at > NOW() - INTERVAL '30 days'
            GROUP BY src, camp
            ORDER BY paid DESC, visits DESC
            LIMIT 30
        """)
        recent = await c.fetch("""
            SELECT id, created_at, status, title, COALESCE(email,'—') AS email,
                   paid_at IS NOT NULL AS paid
            FROM web_orders
            ORDER BY created_at DESC
            LIMIT 20
        """)
        fb_count = await c.fetchval("SELECT COUNT(*) FROM feedback WHERE status='new'")
    rows_utm = "\n".join(
        f"<tr><td>{r['src']}</td><td>{r['camp']}</td>"
        f"<td>{r['visits']}</td><td>{r['lead']}</td><td>{r['paid']}</td>"
        f"<td>{(r['paid']*100//r['visits']) if r['visits'] else 0}%</td></tr>"
        for r in utm)
    rows_recent = "\n".join(
        f"<tr><td>{r['id']}</td><td>{r['created_at'].strftime('%Y-%m-%d %H:%M')}</td>"
        f"<td>{r['status']}{'  💜' if r['paid'] else ''}</td>"
        f"<td>{(r['title'] or '—')[:40]}</td><td>{r['email']}</td></tr>"
        for r in recent)
    cr = (funnel['paid']*100//funnel['total']) if funnel['total'] else 0
    return HTMLResponse(
        f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Admin · Stats</title>
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
<div class="kpis">
  <div class="kpi"><b>{funnel['total']}</b><span>Всего заказов</span></div>
  <div class="kpi"><b>{funnel['paid']}</b><span>Оплачено · CR {cr}%</span></div>
  <div class="kpi"><b>{funnel['done']}</b><span>Сказок собрано</span></div>
  <div class="kpi"><b>{funnel['failed']}</b><span>Failed</span></div>
  <div class="kpi"><b>{funnel['last_24h']}</b><span>За 24ч</span></div>
  <div class="kpi"><b>{funnel['paid_24h']}</b><span>Оплат за 24ч</span></div>
  <div class="kpi"><b>{fb_count}</b><span>Новых отзывов</span></div>
</div>
<h2>UTM-кампании (30 дней)</h2>
<table><tr><th>source</th><th>campaign</th><th>visits</th><th>lead</th><th>paid</th><th>CR</th></tr>
{rows_utm or '<tr><td colspan="6">пока нет данных</td></tr>'}</table>
<h2>Последние заказы</h2>
<table><tr><th>id</th><th>создан</th><th>status</th><th>title</th><th>email</th></tr>
{rows_recent}</table>
</body></html>""")
