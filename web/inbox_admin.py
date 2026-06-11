# -*- coding: utf-8 -*-
"""Admin endpoints for the skazik inbox + reply flow.

Token-gated under the existing ADMIN_TOKEN. Three endpoints:

  GET  /admin/inbox?token=…           — paginated list of messages
  GET  /admin/inbox/<id>?token=…      — single thread + draft reply
  POST /admin/inbox/<id>/reply        — send a reply through the mailer

Replies go out via the same email_queue worker the rest of skazik uses,
so we keep retries / outbox accounting. The threading headers
(In-Reply-To, References) are wired up so the user's mail client groups
the conversation correctly.
"""
from __future__ import annotations

import html as _html
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import db.database as dbmod
from web.email_queue import enqueue_email

router = APIRouter()


def _esc(s) -> str:
    if s is None:
        return ""
    return _html.escape(str(s))


def _auth(request: Request, token: str) -> bool:
    return bool(token) and token == os.environ.get("ADMIN_TOKEN")


@router.get("/admin/inbox", response_class=HTMLResponse)
async def admin_inbox(request: Request, token: str = "", status: str = "new", page: int = 1):
    if not _auth(request, token):
        return HTMLResponse("Forbidden", status_code=403)
    page = max(1, int(page or 1))
    limit, offset = 30, (page - 1) * 30
    async with dbmod._pool.acquire() as c:
        rows = await c.fetch(
            "SELECT id, from_addr, from_name, subject, received_at, status, "
            "       order_id, feedback_id, LEFT(body_text, 200) AS preview "
            "FROM inbox_messages "
            "WHERE ($1 = 'all' OR status = $1) "
            "ORDER BY received_at DESC LIMIT $2 OFFSET $3",
            status, limit, offset,
        )
        counts = await c.fetch(
            "SELECT status, COUNT(*) AS n FROM inbox_messages GROUP BY status"
        )
    count_by = {r["status"]: r["n"] for r in counts}
    total = sum(count_by.values())

    def status_chip(s, label):
        cls = "chip selected" if status == s else "chip"
        href = f"/admin/inbox?token={token}&status={s}"
        n = count_by.get(s, 0) if s != "all" else total
        return f'<a class="{cls}" href="{href}">{label} <b>{n}</b></a>'

    chips = "".join([
        status_chip("new",      "Новые"),
        status_chip("replied",  "Отвечено"),
        status_chip("archived", "В архиве"),
        status_chip("all",      "Все"),
    ])

    rows_html = "\n".join(
        f'<tr class="r {_esc(r["status"])}" onclick="location=\'/admin/inbox/{r["id"]}?token={token}\'">'
        f'<td>{_esc(r["received_at"]).split(".")[0]}</td>'
        f'<td><b>{_esc(r["from_name"] or r["from_addr"])}</b><div class="from">{_esc(r["from_addr"])}</div></td>'
        f'<td><div class="subj">{_esc(r["subject"] or "(no subject)")}</div>'
        f'<div class="preview">{_esc(r["preview"] or "")}</div></td>'
        f'<td>{f"<a class=lnk href=https://skazik.app/order/{_esc(r['order_id'])} target=_blank>{_esc(r['order_id'])[:8]}</a>" if r["order_id"] else "—"}</td>'
        f'<td><span class="st {_esc(r["status"])}">{_esc(r["status"])}</span></td>'
        f"</tr>"
        for r in rows
    ) or '<tr><td colspan=5 class=empty>Пусто</td></tr>'

    page_html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Inbox — Сказик admin</title>
<style>
body{{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;background:#fbf7ff;color:#2b2350;margin:0;padding:24px}}
.wrap{{max-width:1200px;margin:0 auto}}
h1{{margin:0 0 14px}}
.chips{{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 18px}}
.chip{{background:#fff;border:1.5px solid #e4dcfb;color:#241c44;border-radius:999px;
       padding:6px 14px;font-size:13.5px;font-weight:600;cursor:pointer;text-decoration:none}}
.chip:hover{{background:#f1ebff;border-color:#c9b8ff}}
.chip.selected{{background:#7c5cff;border-color:#7c5cff;color:#fff}}
.chip b{{margin-left:6px;font-weight:800}}
table{{width:100%;background:#fff;border-radius:14px;box-shadow:0 6px 20px rgba(43,35,80,.06);
       border-collapse:collapse;overflow:hidden;font-size:13.5px}}
th,td{{padding:10px 14px;text-align:left;vertical-align:top}}
th{{background:#f1ebff;color:#241c44;font-weight:700;text-transform:uppercase;font-size:11.5px;letter-spacing:.04em}}
tr.r{{cursor:pointer;border-bottom:1px solid #f1ebff}}
tr.r:hover{{background:#f6efff}}
tr.r.replied{{opacity:.65}}
.from{{font-size:11.5px;color:#9d92be}}
.subj{{font-weight:600;margin-bottom:2px}}
.preview{{color:#6b6390;font-size:12.5px;max-height:38px;overflow:hidden}}
.st{{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:700}}
.st.new{{background:#ffe7f0;color:#a01c5a}}
.st.replied{{background:#dff5e4;color:#0d6e30}}
.st.archived{{background:#f1ebff;color:#6b6390}}
.lnk{{color:#7c5cff;text-decoration:none;font-family:ui-monospace,monospace}}
.empty{{text-align:center;padding:40px;color:#9d92be}}
</style></head>
<body><div class=wrap>
<h1>Inbox — Сказик</h1>
<div class=chips>{chips}</div>
<table>
<thead><tr><th>received</th><th>from</th><th>subject</th><th>order</th><th>status</th></tr></thead>
<tbody>{rows_html}</tbody>
</table>
</div></body></html>"""
    return HTMLResponse(page_html)


@router.get("/admin/inbox/{msg_id}", response_class=HTMLResponse)
async def admin_inbox_detail(msg_id: int, request: Request, token: str = ""):
    if not _auth(request, token):
        return HTMLResponse("Forbidden", status_code=403)
    async with dbmod._pool.acquire() as c:
        row = await c.fetchrow("SELECT * FROM inbox_messages WHERE id=$1", msg_id)
        if not row:
            return HTMLResponse("Not found", status_code=404)
        # All messages in the same thread for context
        thread = await c.fetch(
            "SELECT id, from_addr, subject, received_at, body_text, status "
            "FROM inbox_messages "
            "WHERE thread_key = $1 ORDER BY received_at",
            row["thread_key"],
        )
        # Recent skazik order context (if any)
        order = None
        if row["order_id"]:
            order = await c.fetchrow(
                "SELECT id, status, title, LEFT(story_text, 600) AS story_head, "
                "       paid_at, media_order_id FROM web_orders WHERE id=$1",
                row["order_id"])
        # Recent outbound emails to the same recipient — what we already said
        outbound = await c.fetch(
            "SELECT id, subject, status, created_at FROM email_outbox "
            "WHERE to_addr=$1 ORDER BY id DESC LIMIT 5",
            row["from_addr"])

    order_block = ""
    if order:
        link = f"https://skazik.app/order/{order['id']}"
        order_block = (
            f'<div class=card><div class=card-h>📦 Заказ клиента</div>'
            f'<div><b>{_esc(order["title"])}</b> — {_esc(order["status"])}'
            f' {"· оплачен " + str(order["paid_at"])[:19] if order["paid_at"] else ""}</div>'
            f'<a class=lnk href="{link}" target=_blank>{link}</a>'
            f'<div class=story>{_esc(order["story_head"] or "")[:600]}…</div>'
            f'</div>'
        )

    out_block = ""
    if outbound:
        items = "\n".join(
            f'<li>{_esc(o["created_at"]).split(".")[0]} — '
            f'<i>{_esc((o["subject"] or "")[:60])}</i> '
            f'<span class="st {_esc(o["status"])}">{_esc(o["status"])}</span></li>'
            for o in outbound
        )
        out_block = f'<div class=card><div class=card-h>📤 Что мы ему слали</div><ul>{items}</ul></div>'

    thread_html = "\n".join(
        f'<div class=msg>'
        f'<div class=msg-meta><b>{_esc(m["from_addr"])}</b> · {_esc(m["received_at"]).split(".")[0]}'
        f' · <span class="st {_esc(m["status"])}">{_esc(m["status"])}</span></div>'
        f'<div class=msg-subj>{_esc(m["subject"] or "")}</div>'
        f'<pre class=msg-body>{_esc(m["body_text"] or "")}</pre>'
        f'</div>'
        for m in thread
    )

    reply_subj = row["subject"] or ""
    if not reply_subj.lower().startswith("re:"):
        reply_subj = "Re: " + reply_subj

    page_html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>{_esc(row["subject"] or "")} — Inbox</title>
<style>
body{{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;background:#fbf7ff;color:#2b2350;margin:0;padding:24px}}
.wrap{{max-width:1100px;margin:0 auto}}
.back{{color:#7c5cff;text-decoration:none;font-weight:600;font-size:14px}}
h1{{margin:8px 0 4px}}
.from{{color:#6b6390;font-size:14px;margin:0 0 18px}}
.card{{background:#fff;border:1.5px solid #e4dcfb;border-radius:14px;padding:16px;margin:0 0 14px;
  box-shadow:0 6px 20px rgba(43,35,80,.06)}}
.card-h{{font-weight:700;margin-bottom:8px;color:#241c44}}
.msg{{background:#fff;border-left:3px solid #c9b8ff;padding:10px 14px;margin:0 0 12px;border-radius:0 12px 12px 0}}
.msg-meta{{font-size:12px;color:#6b6390}}
.msg-subj{{font-weight:600;font-size:14px;margin:4px 0}}
.msg-body{{white-space:pre-wrap;margin:6px 0 0;font:13.5px/1.55 ui-monospace,Menlo,monospace;color:#241c44}}
.story{{color:#6b6390;font-size:13px;line-height:1.5;margin-top:6px}}
.lnk{{color:#7c5cff;text-decoration:none;font-family:ui-monospace,monospace;font-size:12.5px}}
.st{{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:700}}
.st.new{{background:#ffe7f0;color:#a01c5a}}
.st.replied{{background:#dff5e4;color:#0d6e30}}
.st.sent{{background:#dff5e4;color:#0d6e30}}
.st.queued{{background:#fff8e1;color:#7a5a00}}
form{{background:#fff;border:1.5px solid #c9b8ff;border-radius:14px;padding:16px;
   box-shadow:0 6px 20px rgba(43,35,80,.06)}}
label{{display:block;font-weight:700;margin:0 0 6px}}
input[type=text],textarea{{width:100%;border:1.5px solid #e4dcfb;border-radius:10px;padding:11px;
  font:inherit;font-size:14px;background:#fff;color:#241c44}}
textarea{{min-height:240px;resize:vertical;font-family:inherit}}
.row{{display:flex;gap:10px;align-items:center;margin-top:14px}}
button.send{{background:linear-gradient(135deg,#7c5cff,#ff7eb6);color:#fff;border:0;
  font-weight:800;font-size:15px;padding:11px 22px;border-radius:12px;cursor:pointer}}
button.arch{{background:#fff;color:#6b6390;border:1.5px solid #e4dcfb;
  font:inherit;font-weight:600;font-size:14px;padding:10px 16px;border-radius:12px;cursor:pointer}}
ul{{margin:6px 0 0;padding-left:20px;font-size:13px}}
i{{font-style:normal;color:#6b6390}}
</style></head>
<body><div class=wrap>
<a class=back href="/admin/inbox?token={token}">← к списку</a>
<h1>{_esc(row["subject"] or "(no subject)")}</h1>
<div class=from>{_esc(row["from_name"] or "")} &lt;{_esc(row["from_addr"])}&gt; · {_esc(row["received_at"]).split(".")[0]}</div>

{order_block}
{out_block}

<div class=card><div class=card-h>📨 Тред ({len(thread)} сообщений)</div>
{thread_html}
</div>

<form method="post" action="/admin/inbox/{row["id"]}/reply?token={token}">
  <label>Subject</label>
  <input type="text" name="subject" value="{_esc(reply_subj)}">
  <label style="margin-top:14px">Текст ответа</label>
  <textarea name="body_text" placeholder="Напишите ответ…"></textarea>
  <div class=row>
    <button type="submit" class=send>Отправить через papa@skazik.app</button>
    <button type="submit" name="archive" value="1" class=arch>Сохранить и архив</button>
  </div>
</form>

</div></body></html>"""
    return HTMLResponse(page_html)


@router.post("/admin/inbox/{msg_id}/reply")
async def admin_inbox_reply(msg_id: int, request: Request, token: str = "",
                              subject: str = Form(...), body_text: str = Form(...),
                              archive: str = Form("")):
    if not _auth(request, token):
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)
    async with dbmod._pool.acquire() as c:
        row = await c.fetchrow("SELECT * FROM inbox_messages WHERE id=$1", msg_id)
        if not row:
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)

    body_text = (body_text or "").strip()
    if not body_text:
        return JSONResponse({"ok": False, "error": "empty"}, status_code=400)

    # Minimal HTML wrapper so html mail clients render cleanly
    body_html = (
        '<!DOCTYPE html><html><body style="font:14.5px/1.55 -apple-system,Segoe UI,Roboto,Arial;color:#241c44">'
        f'<div style="white-space:pre-wrap">{_html.escape(body_text)}</div>'
        '<div style="color:#6b6390;font-size:13px;margin-top:18px;border-top:1px solid #e4dcfb;padding-top:10px">'
        'С теплом,<br>команда Сказик<br>https://skazik.app'
        '</div></body></html>'
    )

    # Build threading headers so the recipient's client groups the message.
    refs = []
    if row["references"]:
        refs.append(row["references"])
    if row["message_id"]:
        refs.append("<" + row["message_id"] + ">")
    headers_meta = {
        "type": "inbox_reply",
        "inbox_id": msg_id,
        "in_reply_to": "<" + row["message_id"] + ">" if row["message_id"] else None,
        "references": " ".join(refs) if refs else None,
    }

    # enqueue_email reuses skazik's outbox/worker; we attach the threading
    # headers via meta so the mailer can read them.
    reply_id = await enqueue_email(row["from_addr"], subject, body_text, body_html, meta=headers_meta)

    async with dbmod._pool.acquire() as c:
        new_status = "archived" if archive else "replied"
        await c.execute(
            "UPDATE inbox_messages SET status=$1, reply_id=$2, replied_at=NOW() WHERE id=$3",
            new_status, reply_id, msg_id,
        )
    return RedirectResponse(f"/admin/inbox?token={token}", status_code=303)
