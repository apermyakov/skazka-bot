#!/usr/bin/env python3
"""Playwright end-to-end check for the lalaka /order pay flow.

Captures:
- Console errors + every popup event
- Network failures
- Whether the popup actually opens (modal visible)
- What URL the user ends up on if it doesn't

Run: python3 scripts/test_pay_flow_playwright.py <oid>
"""
import asyncio, sys, json, time
from pathlib import Path

OID = sys.argv[1] if len(sys.argv) > 1 else None
OUT = Path("/tmp/pw_screens"); OUT.mkdir(parents=True, exist_ok=True)


async def main():
    from playwright.async_api import async_playwright
    if not OID:
        print("ERROR: pass an oid arg. Usage: test_pay_flow_playwright.py <oid>")
        sys.exit(2)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await ctx.new_page()

        console_msgs, errors, net_fails = [], [], []
        page.on("console", lambda m: console_msgs.append(f"[{m.type}] {m.text}"))
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("requestfailed", lambda r: net_fails.append(
            f"{r.method} {r.url} → {r.failure}"
        ))

        print(f"\n  step 1 — open /order/{OID}")
        url = f"https://lalaka.ai/order/{OID}"
        await page.goto(url, wait_until="networkidle", timeout=30000)
        await page.screenshot(path=str(OUT / "1_order_loaded.png"), full_page=True)

        # Wait for the Pay button to appear (text_ready state)
        try:
            await page.wait_for_selector("#pay", timeout=15000)
        except Exception as e:
            print(f"  ✗ no Pay button after 15s: {e}")
            print(f"  page title: {await page.title()}")
            html_snippet = (await page.content())[:400]
            print(f"  html start: {html_snippet}")
            await browser.close()
            sys.exit(1)

        pay_btn_text = await page.text_content("#pay")
        print(f"  ✓ Pay button found: {pay_btn_text!r}")

        print("\n  step 2 — click Pay, observe what happens")
        await page.click("#pay")
        # Give FastSpring time to actually paint the popup container
        await page.wait_for_timeout(6000)

        # Wait for either:
        # - FS Popup modal (iframe with FS payment form)
        # - Window navigation to FS error page
        # - An error flash banner
        try:
            await page.wait_for_function(
                """() => {
                    const fsIframes = document.querySelectorAll('iframe[src*="onfastspring"], iframe[id*="fsc"]');
                    if (fsIframes.length > 0) return 'popup-opened';
                    if (location.href.includes('onfastspring.com')) return 'redirected';
                    const banner = document.querySelector('.ibanner.on');
                    if (banner) return 'inline-error';
                    return null;
                }""",
                timeout=15000,
            )
        except Exception:
            pass

        # Snapshot post-click state
        await page.screenshot(path=str(OUT / "2_after_pay_click.png"), full_page=True)
        cur_url = page.url
        print(f"  url after click: {cur_url}")

        # Dump iframe state — visibility, size, content text
        for sel in ['iframe[src*="onfastspring"]', 'iframe[id*="fsc"]', 'iframe']:
            ifs = await page.query_selector_all(sel)
            if not ifs: continue
            print(f"\n  iframes matching {sel!r}: {len(ifs)}")
            for i, fr in enumerate(ifs):
                box = await fr.bounding_box()
                vis = await fr.is_visible()
                src = await fr.get_attribute("src")
                print(f"    [{i}] visible={vis} box={box} src={(src or '')[:120]}")
            break

        # Try to inspect inside the FS iframe for actual checkout content
        try:
            fs_frames = [f for f in page.frames if "onfastspring" in (f.url or "")]
            if fs_frames:
                fr = fs_frames[0]
                print(f"\n  FS iframe frame URL: {fr.url}")
                # Title or body content?
                t = await fr.title()
                print(f"  FS iframe title: {t!r}")
                # First 400 chars of body text
                body = (await fr.evaluate("document.body && document.body.innerText") or "")[:500]
                print(f"  FS iframe body[:500]: {body!r}")
        except Exception as e:
            print(f"  iframe inspect failed: {e}")

        # Did we navigate away?
        if "onfastspring.com" in cur_url:
            print(f"  ⚠ NAVIGATED to FastSpring — popup didn't open, fell through to redirect")
            # Capture what FS shows
            body_text = (await page.text_content("body") or "")[:800]
            print(f"  FS page body[:800]: {body_text!r}")
        else:
            iframes = await page.query_selector_all('iframe[src*="onfastspring"], iframe[id*="fsc"]')
            if iframes:
                print(f"  ✓ POPUP iframe detected ({len(iframes)} matches)")
                for i, fr in enumerate(iframes):
                    src = await fr.get_attribute("src")
                    print(f"    iframe[{i}]: {src}")
            else:
                print(f"  ✗ no popup, no redirect — clicked but nothing happened")

        # Pull what our error-callback caught
        last_err = await page.evaluate("window.__fs_last_error || null")
        if last_err:
            print(f"\n  FastSpring callback error payload:")
            print(f"    {json.dumps(last_err, ensure_ascii=False, indent=2)[:600]}")

        # Console output
        print("\n  ─── Console (first 30) ───")
        for m in console_msgs[:30]:
            print(f"    {m[:200]}")
        if errors:
            print("\n  ─── Page errors ───")
            for e in errors[:5]:
                print(f"    {e[:300]}")
        if net_fails:
            print("\n  ─── Network failures ───")
            for f in net_fails[:10]:
                print(f"    {f[:300]}")

        print(f"\n  screenshots: {OUT}/1_order_loaded.png  {OUT}/2_after_pay_click.png")
        await browser.close()


asyncio.run(main())
