#!/usr/bin/env python3
"""Poll FastSpring storefront every 5 min — alert when it transitions
from HTTP 500 (Store Error) to a real page.

The storefront returns 500 while account activation is pending. Once
FS approves onboarding, the same URL flips to a real checkout page.
This watcher lets us catch that flip the moment it happens instead of
checking manually.

Run: python3 scripts/monitor_fastspring_activation.py &
Set FASTSPRING_NOTIFY_TG_BOT_TOKEN + FASTSPRING_NOTIFY_TG_CHAT_ID env
vars to get a Telegram ping; otherwise it just prints.
"""
import asyncio, os, time
import aiohttp

CHECK_URL = "https://lalakaai.test.onfastspring.com/popup-lalakaai/lalaka-fairy-tale"
INTERVAL = 5 * 60  # 5 minutes


async def notify(text: str):
    print(f"  [notify] {text}")
    bot = os.environ.get("FASTSPRING_NOTIFY_TG_BOT_TOKEN") or os.environ.get("BOT_TOKEN")
    chat = os.environ.get("FASTSPRING_NOTIFY_TG_CHAT_ID") or os.environ.get("ADMIN_IDS", "").split(",")[0]
    if not bot or not chat:
        return
    url = f"https://api.telegram.org/bot{bot}/sendMessage"
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json={"chat_id": chat, "text": text}) as r:
            await r.text()


async def main():
    last = None
    while True:
        async with aiohttp.ClientSession() as s:
            try:
                async with s.get(CHECK_URL, timeout=aiohttp.ClientTimeout(total=15)) as r:
                    text = await r.text()
                    has_error = "Store Error" in text or r.status == 500
            except Exception as e:
                print(f"  {time.strftime('%H:%M')}: probe error {e}")
                has_error = None
        state = "blocked" if has_error else "live"
        now = time.strftime("%Y-%m-%d %H:%M")
        if state != last:
            msg = f"🚨 FastSpring storefront state: {last} → {state} ({now})"
            print(msg)
            if last is not None:
                await notify(msg)
            last = state
        else:
            print(f"  {now}: {state}")
        await asyncio.sleep(INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
