"""Daily Skazik report → Telegram admin.

Run via cron: `docker compose exec -T web python /app/scripts/daily_report.py`
Sends one message summarising last 24h:
  - Real customer funnel (founders/tests filtered out)
  - Top UTM sources that brought paid orders
  - Direct: cost, clicks, conversions per campaign (yesterday)
  - New feedback (with preview)
  - Errors (count + last error text if any)
  - Email queue health
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

import aiohttp

sys.path.insert(0, "/app")


async def db_stats():
    import db.database as db
    from bot.config import settings
    await db.init_db()
    excluded = [e.strip().lower() for e in
                os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()]
    async with db._pool.acquire() as c:
        funnel = await c.fetchrow("""
            SELECT
              COUNT(*) AS visits,
              COUNT(*) FILTER (WHERE status IN ('text_ready','generating','done','awaiting_payment','abandoned','failed')) AS leads,
              COUNT(*) FILTER (WHERE paid_at IS NOT NULL) AS paid,
              COUNT(*) FILTER (WHERE status='done') AS done,
              COUNT(*) FILTER (WHERE status='failed') AS failed,
              COUNT(*) FILTER (WHERE status='abandoned') AS abandoned
            FROM web_orders
            WHERE created_at > NOW() - INTERVAL '24 hours'
              AND NOT (LOWER(COALESCE(email,'')) = ANY($1::text[]))
        """, excluded)
        utm_top = await c.fetch("""
            SELECT
              COALESCE(utm_source,'(direct)') AS src,
              COALESCE(utm_campaign,'-') AS camp,
              COUNT(*) AS visits,
              COUNT(*) FILTER (WHERE paid_at IS NOT NULL) AS paid
            FROM web_orders
            WHERE created_at > NOW() - INTERVAL '24 hours'
              AND NOT (LOWER(COALESCE(email,'')) = ANY($1::text[]))
            GROUP BY src, camp ORDER BY paid DESC, visits DESC LIMIT 5
        """, excluded)
        feedback_new = await c.fetch("""
            SELECT name, email, message, created_at
            FROM feedback
            WHERE status='new' AND created_at > NOW() - INTERVAL '24 hours'
            ORDER BY created_at DESC LIMIT 5
        """)
        err_24h = await c.fetchval(
            "SELECT COUNT(*) FROM errors WHERE created_at > NOW() - INTERVAL '24 hours'") or 0
        last_err = await c.fetchrow(
            "SELECT phase, error_type, error_message, created_at FROM errors "
            "WHERE created_at > NOW() - INTERVAL '24 hours' ORDER BY created_at DESC LIMIT 1")
        email_q = await c.fetchrow("""
            SELECT COUNT(*) FILTER (WHERE status='queued') AS queued,
                   COUNT(*) FILTER (WHERE status='failed') AS failed,
                   COUNT(*) FILTER (WHERE status='queued' AND next_try_at < NOW() - INTERVAL '15 min') AS stuck
            FROM email_outbox
        """)
        bot_24h = await c.fetchval(
            "SELECT COUNT(*) FROM stories WHERE created_at > NOW() - INTERVAL '24 hours'") or 0
        bot_done_24h = await c.fetchval(
            "SELECT COUNT(*) FROM stories WHERE status='completed' AND completed_at > NOW() - INTERVAL '24 hours'") or 0
    return {
        "funnel": funnel, "utm_top": utm_top, "feedback_new": feedback_new,
        "err_24h": err_24h, "last_err": last_err, "email_q": email_q,
        "bot_24h": bot_24h, "bot_done_24h": bot_done_24h,
    }


async def direct_stats():
    """Yesterday's spend/clicks/conversions per campaign."""
    token = os.environ.get("YANDEX_DIRECT_TOKEN", "")
    if not token:
        return None
    yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
    payload = {
        "params": {
            "SelectionCriteria": {"Filter": [{"Field": "CampaignId",
                "Operator": "IN", "Values": ["710328773", "710328840"]}]},
            "FieldNames": ["CampaignName", "Impressions", "Clicks", "Cost", "Conversions"],
            "ReportName": f"skazik-{yesterday}", "ReportType": "CAMPAIGN_PERFORMANCE_REPORT",
            "DateRangeType": "YESTERDAY", "Format": "TSV", "IncludeVAT": "YES",
        }
    }
    h = {"Authorization": f"Bearer {token}", "Accept-Language": "ru",
         "Content-Type": "application/json",
         "processingMode": "online", "returnMoneyInMicros": "false"}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post("https://api.direct.yandex.com/json/v5/reports",
                              json=payload, headers=h) as r:
                if r.status == 200:
                    return await r.text()
                if r.status in (201, 202):
                    # Report still processing — skip in daily, will catch next time
                    return "processing"
                return f"err {r.status}: {(await r.text())[:200]}"
    except Exception as e:
        return f"err: {e}"


def fmt_direct(tsv: str | None) -> str:
    if not tsv or tsv == "processing":
        return "  (отчёт ещё готовится в Direct)" if tsv == "processing" else "  (нет данных)"
    if tsv.startswith("err"):
        return f"  ⚠️ {tsv}"
    lines = [l for l in tsv.splitlines() if l and not l.startswith(("Total", "Date", "ReportName", '"'))]
    # First line headers, then data
    out = []
    for l in lines[1:]:
        parts = l.split("\t")
        if len(parts) >= 4:
            try:
                name, impr, clicks, cost = parts[0], int(parts[1]), int(parts[2]), float(parts[3])
                conv = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
                out.append(f"  • {name}: показы {impr}, клики {clicks}, ₽{cost:.0f}"
                           + (f", цели {conv}" if conv else ""))
            except Exception:
                pass
    return "\n".join(out) if out else "  (нет показов вчера)"


async def send_tg(text: str):
    token = os.environ["BOT_TOKEN"]
    admins = os.environ["ADMIN_IDS"].split(",")
    async with aiohttp.ClientSession() as s:
        for aid in admins:
            try:
                await s.post(f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": aid.strip(), "text": text, "parse_mode": "HTML",
                          "disable_web_page_preview": True})
            except Exception:
                pass


def esc(s):
    return (s or "").replace("<", "&lt;").replace(">", "&gt;")


async def main():
    stats = await db_stats()
    direct = await direct_stats()

    f = stats["funnel"]
    msg = [
        "📊 <b>Skazik · дневной отчёт</b>",
        f"<i>{datetime.now().strftime('%Y-%m-%d %H:%M')} МСК</i>",
        "",
        "<b>Web (за 24ч, без founder/test):</b>",
        f"• Заказов создано: <b>{f['visits']}</b>",
        f"• Дошли до текста (лиды): <b>{f['leads']}</b>",
        f"• Оплатили: <b>{f['paid']}</b>",
        f"• Готовых сказок: <b>{f['done']}</b>",
        f"• Сорвалось (failed): <b>{f['failed']}</b>",
        f"• Бросили оплату (abandoned): <b>{f['abandoned']}</b>",
        "",
        f"<b>Бот за 24ч:</b> {stats['bot_24h']} начато, {stats['bot_done_24h']} завершено",
        "",
        "<b>Топ UTM (24ч):</b>",
    ]
    if stats["utm_top"]:
        for r in stats["utm_top"]:
            msg.append(f"  • {r['src']}/{r['camp']}: visits={r['visits']}, paid={r['paid']}")
    else:
        msg.append("  (пока нет заказов от реальных юзеров)")

    msg += ["", "<b>Яндекс.Директ (вчера):</b>", fmt_direct(direct)]

    eq = stats["email_q"]
    stuck_mark = " ⚠️" if eq["stuck"] else ""
    msg += ["",
            f"<b>Email queue:</b> {eq['queued']} в очереди{stuck_mark}, {eq['failed']} failed"]

    if stats["err_24h"]:
        msg += ["", f"⚠️ <b>Ошибок за 24ч: {stats['err_24h']}</b>"]
        if stats["last_err"]:
            le = stats["last_err"]
            msg.append(f"  Последняя [{esc(le['phase'] or '')}/{esc(le['error_type'] or '')}]: "
                       f"<code>{esc(le['error_message'] or '')[:200]}</code>")
    else:
        msg += ["", "✅ <b>Ошибок нет</b>"]

    if stats["feedback_new"]:
        msg += ["", f"💬 <b>Новые отзывы ({len(stats['feedback_new'])}):</b>"]
        for r in stats["feedback_new"]:
            name = esc(r["name"] or "—"); email = esc(r["email"] or "—")
            preview = esc((r["message"] or "")[:160])
            msg.append(f"  • <b>{name}</b> ({email}):\n    {preview}")

    msg += ["",
            "<a href='https://skazik.app/admin/stats?token=75993ec428aeaa1eec4ee796a90dba25'>📈 Открыть admin/stats</a>"]

    text = "\n".join(msg)
    # Telegram limit 4096 chars
    if len(text) > 4000:
        text = text[:3900] + "…"
    await send_tg(text)
    print("sent, length=", len(text))


if __name__ == "__main__":
    asyncio.run(main())
