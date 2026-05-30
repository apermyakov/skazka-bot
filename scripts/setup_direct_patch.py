"""Patch: add missing keywords + the failed 3rd ad to existing
СКАЗИК — Поиск draft campaign (id 710328773, adgroup 5756064571).
"""
import asyncio
import json
import os

import aiohttp

API = "https://api.direct.yandex.com/json/v5"
TOKEN = os.environ["YANDEX_DIRECT_TOKEN"]
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept-Language": "ru",
    "Content-Type": "application/json",
}

CAMPAIGN_ID = 710328773
ADGROUP_ID = 5756064571


async def call(s, endpoint, payload):
    async with s.post(f"{API}/{endpoint}", headers=HEADERS, json=payload) as r:
        return await r.json()


async def add_third_ad(s):
    """Re-add the 3rd ad with text trimmed to ≤81 chars."""
    base = "https://skazik.app/"
    utm = "?utm_source=yandex&utm_medium=search&utm_campaign=skazik_search&utm_content=ad_3"
    ad = {
        "AdGroupId": ADGROUP_ID,
        "TextAd": {
            "Title": "Аудиосказка на ночь",
            "Title2": "С именем и фото вашего малыша",
            "Text": "Тёплая женская озвучка, добрый сюжет. Готово за 5 минут.",  # 56 chars
            "Mobile": "NO",
            "Href": base + utm,
            "DisplayUrlPath": "skazka-na-noch",
        },
    }
    data = await call(s, "ads", {"method": "add", "params": {"Ads": [ad]}})
    print("3rd AD:", json.dumps(data, ensure_ascii=False)[:400])


async def add_keywords(s):
    """Correct field name is 'Keyword', not 'KeywordOrPhrase'."""
    keywords = [
        "сказка с именем ребенка",
        "персональная сказка",
        "сказка по фото ребенка",
        "сказка с именем",
        "индивидуальная сказка ребенку",
        "аудиосказка с именем",
        "персональная аудиосказка",
        "сказка где герой мой ребенок",
        "сделать сказку ребенку с именем",
        "заказать сказку с именем ребенка",
        "сказка про моего сына",
        "сказка про мою дочку",
        "персонализированная сказка для детей",
        "сказка на день рождения ребенка персональная",
        "сказка подарок ребенку с именем",
    ]
    items = [{"Keyword": k, "AdGroupId": ADGROUP_ID, "Bid": 5_000_000} for k in keywords]
    data = await call(s, "keywords", {"method": "add", "params": {"Keywords": items}})
    print("KEYWORDS:", json.dumps(data, ensure_ascii=False)[:700])
    ok = sum(1 for r in data.get("result", {}).get("AddResults", []) if r.get("Id"))
    print(f"  ✓ added {ok}/{len(keywords)}")


async def add_minus_words(s):
    """Campaign-level negative keywords to filter junk traffic."""
    minus = [
        "бесплатно", "скачать", "торрент", "русские народные",
        "читать онлайн", "без регистрации", "смотреть",
        "аудиокнига", "для взрослых", "страшная",
    ]
    payload = {
        "method": "update",
        "params": {
            "Campaigns": [{
                "Id": CAMPAIGN_ID,
                "TextCampaign": {
                    "NegativeKeywords": {"Items": minus},
                },
            }],
        },
    }
    data = await call(s, "campaigns", payload)
    print("MINUS:", json.dumps(data, ensure_ascii=False)[:400])


async def main():
    async with aiohttp.ClientSession() as s:
        await add_third_ad(s)
        await add_keywords(s)
        await add_minus_words(s)


if __name__ == "__main__":
    asyncio.run(main())
