"""Audit skazik orders for name mismatch between topic and story title.

The bug: user types "Сказка про Лёху" but the LLM writes a story about
"Тимофей". Catch every such case by extracting the first capitalised
Cyrillic token from the topic (likely the child's name) and comparing
it to the names that appear in the title / first paragraph of the story.

A name is considered "missing" if the topic's name token doesn't appear
in either the title or the first 200 chars of the story body. Renders
an HTML report to web/static/skazik_name_audit.html.
"""
import asyncio, re, os, html, datetime
import asyncpg

OUT_HTML = "/app/web/static/skazik_name_audit.html"

# Cyrillic capitalised tokens (3-30 chars), excluding obvious common words
RU_NAME_RX = re.compile(r"\b([А-ЯЁ][а-яё]{2,29})\b")
STOP_WORDS = {"Сказка","Сказку","Сказке","Сказкой","Сказки","Сказок",
              "Бабушка","Дедушка","Мама","Папа","Мать","Отец","Тётя","Дядя","Сестра","Брат",
              "Принц","Принцесса","Принцессе","Дракон","Лиса","Волк","Медведь","Заяц","Кот","Кошка","Собака",
              "Подруги","Подруга","Подружки","Друзья","Друг","Дети","Девочки","Мальчики",
              "Однажды","Жил","Жила","Жили","Если","Когда","Всё","Это","Чтобы","Тогда",
              "Поэтому","Может","Должен","Должна","Нет","Очень","Любит","Очень","Любящая",
              "Имя","Имени","Фамилия","Фамилии","Утром","Вечером","Ночью","Утро","Вечер","День","Ночь",
              "Лёня","Лет","Год","Года","Месяц","Дни","Утром"}


def first_name(topic: str) -> str | None:
    """First capitalised Cyrillic word in topic that isn't a stopword.
    Skips the first match if it's 'Сказка' (the literal "fairy tale" word).
    """
    if not topic:
        return None
    for m in RU_NAME_RX.finditer(topic[:300]):
        token = m.group(1)
        if token not in STOP_WORDS:
            return token
    return None


def name_present_in(text: str, name: str) -> bool:
    """Does any morphological variant of the name show up?

    Russian names decline aggressively (Вика → Вику → Вике → Викой → Вики).
    We take a 3-char prefix as the stem; that's lossy but catches all common
    cases without producing false positives the way a 5-char stem would.
    """
    if not text or not name:
        return False
    stem = name[:3].lower()
    if len(stem) < 3:
        return name.lower() in text.lower()
    return stem in text.lower()


async def main():
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=2)
    rows = await pool.fetch("""
        SELECT id, topic, title, LEFT(story_text, 400) as story_head, status, created_at, email
        FROM web_orders
        WHERE story_text IS NOT NULL
          AND topic IS NOT NULL
          AND created_at > NOW() - INTERVAL '30 days'
        ORDER BY created_at DESC
    """)
    await pool.close()

    flagged, ok, missing_name = [], 0, 0
    for r in rows:
        name = first_name(r["topic"] or "")
        if not name:
            missing_name += 1
            continue
        title_has = name_present_in(r["title"] or "", name)
        story_has = name_present_in(r["story_head"] or "", name)
        if not (title_has or story_has):
            flagged.append({
                "id": r["id"],
                "topic": r["topic"],
                "expected_name": name,
                "title": r["title"] or "",
                "story_head": (r["story_head"] or "")[:200],
                "status": r["status"],
                "created_at": str(r["created_at"])[:19],
                "email": r["email"] or "",
            })
        else:
            ok += 1

    total = len(rows)
    rate = (len(flagged) / total * 100) if total else 0

    # ── render html ────────────────────────────────────────────────────
    rows_html = "\n".join(
        f"<tr>"
        f"<td>{html.escape(f['created_at'])}</td>"
        f"<td><a href='https://skazik.app/order/{html.escape(f['id'])}' target='_blank'>{html.escape(f['id'])}</a></td>"
        f"<td><b>{html.escape(f['expected_name'])}</b></td>"
        f"<td>{html.escape(f['title'])}</td>"
        f"<td class=topic>{html.escape((f['topic'] or '')[:180])}</td>"
        f"<td class=story>{html.escape(f['story_head'])}</td>"
        f"<td>{html.escape(f['status'])}</td>"
        f"<td>{html.escape(f['email'])}</td>"
        f"</tr>"
        for f in flagged
    )
    page = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Skazik name-mismatch audit — {datetime.datetime.utcnow():%Y-%m-%d %H:%M} UTC</title>
<style>
body{{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;background:#fbf7ff;color:#241c44;margin:0;padding:24px}}
.wrap{{max-width:1400px;margin:0 auto}}
h1{{margin:0 0 8px}} .meta{{color:#6b6390;font-size:14px;margin-bottom:24px}}
.kpis{{display:flex;gap:14px;margin:0 0 22px;flex-wrap:wrap}}
.kpi{{background:#fff;border:1.5px solid #e4dcfb;border-radius:12px;padding:12px 16px;flex:1;min-width:160px}}
.kpi .v{{font-size:24px;font-weight:800}}
.kpi .l{{color:#6b6390;font-size:12px;text-transform:uppercase;letter-spacing:.04em}}
.kpi.bad .v{{color:#c0392b}}
.kpi.ok .v{{color:#1f6f3c}}
table{{width:100%;border-collapse:collapse;background:#fff;border-radius:12px;overflow:hidden;
  box-shadow:0 6px 20px rgba(43,35,80,.06);font-size:13px}}
th,td{{padding:9px 10px;text-align:left;vertical-align:top;border-bottom:1px solid #f1ebff}}
th{{background:#f1ebff;color:#241c44;font-weight:700;font-size:12px;text-transform:uppercase}}
td.topic,td.story{{color:#6b6390;font-size:12.5px;max-width:340px;word-break:break-word}}
td a{{color:#7c5cff;text-decoration:none}} td a:hover{{text-decoration:underline}}
b{{color:#c0392b;font-weight:700}}
.empty{{text-align:center;padding:40px;color:#6b6390}}
</style></head>
<body><div class=wrap>
<h1>Skazik — name-mismatch audit</h1>
<div class=meta>Generated {datetime.datetime.utcnow():%Y-%m-%d %H:%M} UTC.
Last 30 days, {total} orders with story_text scanned.
Flagged when first capitalised Cyrillic token in topic doesn't appear in title or first 200 chars of story.</div>

<div class=kpis>
  <div class=kpi><div class=v>{total}</div><div class=l>Orders scanned</div></div>
  <div class=kpi ok><div class=v>{ok}</div><div class=l>Name matched</div></div>
  <div class="kpi bad"><div class=v>{len(flagged)}</div><div class=l>Mismatched</div></div>
  <div class=kpi><div class=v>{rate:.1f}%</div><div class=l>Mismatch rate</div></div>
  <div class=kpi><div class=v>{missing_name}</div><div class=l>No name extracted</div></div>
</div>

{'<table><thead><tr><th>created</th><th>order id</th><th>expected</th><th>actual title</th><th>topic</th><th>story start</th><th>status</th><th>email</th></tr></thead><tbody>' + rows_html + '</tbody></table>' if flagged else '<div class=empty>✓ No name mismatches detected in the last 30 days.</div>'}

</div></body></html>"""

    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"  scanned: {total}")
    print(f"  matched: {ok}")
    print(f"  flagged: {len(flagged)} ({rate:.1f}%)")
    print(f"  no name: {missing_name}")
    print(f"  report:  {OUT_HTML}")


asyncio.run(main())
