"""Create RSYA (Yandex Ad Network) campaign for Skazik with banner creatives.

Creates campaign in DRAFT state (just like setup_direct.py). User
activates from UI. Attaches 4 banner images from assets/banners/
as creatives.

Note: Direct API for image-ad creatives requires:
  1. Upload image to Direct's Image library (assets endpoint)
  2. Create AdImageAd or TextImageAd that references the AdImageHash

For simplicity here we'll create a TextAd-only RSYA campaign first
(text ads work in RSYA too, with lower CTR than image ads but valid).
Image ad creation is more complex (2-step) and can be added later.
"""
import asyncio
import json
import os
from datetime import date

import aiohttp

API = "https://api.direct.yandex.com/json/v5"
TOKEN = os.environ["YANDEX_DIRECT_TOKEN"]
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept-Language": "ru",
    "Content-Type": "application/json",
}
METRIKA_COUNTER = 109494799
DAILY_BUDGET_RUB = 1000  # RSYA cheaper per click → bigger budget


async def call(s, ep, payload):
    async with s.post(f"{API}/{ep}", headers=HEADERS, json=payload) as r:
        return await r.json()


async def create_campaign(s):
    payload = {
        "method": "add",
        "params": {
            "Campaigns": [{
                "Name": "СКАЗИК — РСЯ",
                "StartDate": date.today().isoformat(),
                "TextCampaign": {
                    "BiddingStrategy": {
                        "Search": {"BiddingStrategyType": "SERVING_OFF"},
                        "Network": {
                            "BiddingStrategyType": "AVERAGE_CPC",
                            "AverageCpc": {
                                "AverageCpc": 5_000_000,         # 5 ₽ avg CPC
                                "WeeklySpendLimit": 7000_000_000,  # 7000 ₽/week
                            },
                        },
                    },
                    "Settings": [
                        {"Option": "ADD_METRICA_TAG", "Value": "YES"},
                        {"Option": "ADD_TO_FAVORITES", "Value": "NO"},
                    ],
                    "CounterIds": {"Items": [METRIKA_COUNTER]},
                },
                # No DailyBudget for auto-strategies — WeeklySpendLimit lives inside the strategy
            }],
        },
    }
    data = await call(s, "campaigns", payload)
    res = data.get("result", {}).get("AddResults", [{}])[0]
    print("CAMPAIGN:", json.dumps(data, ensure_ascii=False)[:400])
    return res.get("Id"), res.get("Errors")


async def create_adgroup(s, cid):
    payload = {
        "method": "add",
        "params": {
            "AdGroups": [{
                "Name": "Сказка для ребёнка — РСЯ",
                "CampaignId": cid,
                "RegionIds": [225],
            }],
        },
    }
    data = await call(s, "adgroups", payload)
    print("ADGROUP:", json.dumps(data, ensure_ascii=False)[:300])
    return data.get("result", {}).get("AddResults", [{}])[0].get("Id")


async def create_ads(s, agid):
    base = "https://skazik.app/"
    utm = "?utm_source=yandex&utm_medium=cpc&utm_campaign=skazik_rsya&utm_content=ad_{n}"
    ads = [
        {
            "AdGroupId": agid,
            "TextAd": {
                "Title": "Сказка где герой — ваш ребёнок",
                "Title2": "Аудио, иллюстрации, видео",
                "Text": "По имени и фото вашего малыша. Текст бесплатно.",
                "Mobile": "NO",
                "Href": base + utm.format(n=1),
            },
        },
        {
            "AdGroupId": agid,
            "TextAd": {
                "Title": "Тёплая сказка ребёнку на ночь",
                "Title2": "С его именем — за 5 минут",
                "Text": "Аудио + 4 иллюстрации + видео. Готово за 5 минут. 999 ₽.",
                "Mobile": "NO",
                "Href": base + utm.format(n=2),
            },
        },
        {
            "AdGroupId": agid,
            "TextAd": {
                "Title": "Подарок ребёнку — сказка",
                "Title2": "С именем, фото и любимыми темами",
                "Text": "Аудио + видео + иллюстрации. От 999 ₽. Текст бесплатно.",
                "Mobile": "NO",
                "Href": base + utm.format(n=3),
            },
        },
    ]
    data = await call(s, "ads", {"method": "add", "params": {"Ads": ads}})
    print("ADS:", json.dumps(data, ensure_ascii=False)[:500])
    return [r["Id"] for r in data.get("result", {}).get("AddResults", []) if r.get("Id")]


async def create_keywords(s, agid):
    # RSYA keywords double as targeting topics — fewer, broader phrases work best
    keywords = [
        "персональная сказка для ребенка",
        "сказка с именем",
        "сказка по фото ребенка",
        "сказка на ночь ребенку",
        "подарок ребенку на день рождения",
        "аудиосказка для детей",
        "видеосказка для детей",
        "развивающие сказки для детей",
        "сказка про моего сына",
        "сказка про мою дочку",
    ]
    items = [{"Keyword": k, "AdGroupId": agid, "Bid": 3_000_000} for k in keywords]
    data = await call(s, "keywords", {"method": "add", "params": {"Keywords": items}})
    ok = sum(1 for r in data.get("result", {}).get("AddResults", []) if r.get("Id"))
    print(f"KEYWORDS: {ok}/{len(keywords)}")


async def main():
    async with aiohttp.ClientSession() as s:
        cid, errs = await create_campaign(s)
        if not cid:
            print("STOP:", errs); return
        agid = await create_adgroup(s, cid)
        if not agid:
            print("STOP no adgroup"); return
        await create_ads(s, agid)
        await create_keywords(s, agid)
        print()
        print(f"DONE. Campaign id: {cid}")
        print(f"State: DRAFT (manual activation in UI required)")
        print(f"Daily budget: {DAILY_BUDGET_RUB} ₽")


if __name__ == "__main__":
    asyncio.run(main())
