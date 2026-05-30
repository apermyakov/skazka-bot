"""Create a Skazik search campaign in Yandex.Direct, fully populated
but in SUSPENDED state — never auto-spends. User activates from UI
after review.

Memory rule (reference_yandex-analytics-ads): NEVER touch existing
Direct campaigns. We only create new ones, prefixed 'СКАЗИК.'

Run: docker compose exec web python /tmp/setup_direct.py
"""
import asyncio
import json
import os
from datetime import date, timedelta

import aiohttp

API = "https://api.direct.yandex.com/json/v5"
TOKEN = os.environ["YANDEX_DIRECT_TOKEN"]
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept-Language": "ru",
    "Content-Type": "application/json",
    "Client-Login": os.environ.get("YANDEX_DIRECT_LOGIN", ""),  # blank=use token's own
}
# Strip empty Client-Login (Direct rejects if header present but blank)
if not HEADERS.get("Client-Login"):
    HEADERS.pop("Client-Login", None)

METRIKA_COUNTER = 109494799
GOAL_PURCHASE = 563655884   # Ecommerce: покупка (has value 999₽ → better for ROI optim)

# We start cautious: low daily budget, manual bids (WB_DEFAULT). User can
# switch to pay-for-conversions in UI later when there's enough data.
DAILY_BUDGET_RUB = 500


async def call(session, endpoint, payload):
    async with session.post(f"{API}/{endpoint}", headers=HEADERS, json=payload) as r:
        return await r.json()


async def create_campaign(session):
    """Returns campaign id."""
    today = date.today()
    payload = {
        "method": "add",
        "params": {
            "Campaigns": [{
                "Name": "СКАЗИК — Поиск (draft)",
                "StartDate": today.isoformat(),
                "TextCampaign": {
                    "BiddingStrategy": {
                        "Search": {"BiddingStrategyType": "HIGHEST_POSITION"},
                        "Network": {"BiddingStrategyType": "SERVING_OFF"},  # Search-only
                    },
                    "Settings": [
                        {"Option": "ADD_METRICA_TAG", "Value": "YES"},
                        {"Option": "ADD_OPENSTAT_TAG", "Value": "NO"},
                        {"Option": "ADD_TO_FAVORITES", "Value": "NO"},
                        {"Option": "ENABLE_AREA_OF_INTEREST_TARGETING", "Value": "YES"},
                        {"Option": "MAINTAIN_NETWORK_CPC", "Value": "NO"},
                    ],
                    "CounterIds": {"Items": [METRIKA_COUNTER]},
                },
                "DailyBudget": {
                    "Amount": DAILY_BUDGET_RUB * 1_000_000,  # micro-rub
                    "Mode": "STANDARD",
                },
                # ClientInfo, Notification can be omitted — defaults to account
            }],
        },
    }
    data = await call(session, "campaigns", payload)
    print("CAMPAIGN ADD:", json.dumps(data, ensure_ascii=False)[:500])
    res = data.get("result", {}).get("AddResults", [{}])[0]
    if res.get("Errors"):
        print("ERRORS:", res["Errors"])
        return None
    return res.get("Id")


async def create_adgroup(session, campaign_id):
    """Returns ad group id."""
    payload = {
        "method": "add",
        "params": {
            "AdGroups": [{
                "Name": "Сказка про ребёнка",
                "CampaignId": campaign_id,
                "RegionIds": [225],  # Россия
                # No NegativeKeywords — added at campaign level via separate call if needed
            }],
        },
    }
    data = await call(session, "adgroups", payload)
    print("ADGROUP:", json.dumps(data, ensure_ascii=False)[:500])
    res = data.get("result", {}).get("AddResults", [{}])[0]
    if res.get("Errors"):
        return None
    return res.get("Id")


async def create_ads(session, adgroup_id):
    """Returns list of ad ids."""
    base_url = "https://skazik.app/"
    utm = "?utm_source=yandex&utm_medium=search&utm_campaign=skazik_search&utm_content=ad_{n}"
    # 3 ad variations — Direct A/B-tests them
    ads = [
        {
            "AdGroupId": adgroup_id,
            "TextAd": {
                "Title": "Сказка с именем вашего ребёнка",
                "Title2": "За 5 минут, текст бесплатно",
                "Text": "Аудиосказка по фото и имени. Тёплая озвучка, иллюстрации, видео. От 999 ₽.",
                "Mobile": "NO",
                "Href": base_url + utm.format(n=1),
                "DisplayUrlPath": "personalnaya-skazka",
            },
        },
        {
            "AdGroupId": adgroup_id,
            "TextAd": {
                "Title": "Персональная сказка ребёнку",
                "Title2": "По фото, имени, любимым темам",
                "Text": "Малыш узнаёт себя в герое. Аудио + иллюстрации + видео. Текст показываем бесплатно.",
                "Mobile": "NO",
                "Href": base_url + utm.format(n=2),
                "DisplayUrlPath": "skazka-pro-rebenka",
            },
        },
        {
            "AdGroupId": adgroup_id,
            "TextAd": {
                "Title": "Аудиосказка на ночь",
                "Title2": "С именем и фото вашего малыша",
                "Text": "Тёплая женская озвучка, добрый сюжет. Готово за 5 минут. Оплата только если понравится.",
                "Mobile": "NO",
                "Href": base_url + utm.format(n=3),
                "DisplayUrlPath": "skazka-na-noch",
            },
        },
    ]
    payload = {"method": "add", "params": {"Ads": ads}}
    data = await call(session, "ads", payload)
    print("ADS:", json.dumps(data, ensure_ascii=False)[:700])
    ids = []
    for res in data.get("result", {}).get("AddResults", []):
        if res.get("Errors"):
            print("AD ERR:", res["Errors"])
        elif res.get("Id"):
            ids.append(res["Id"])
    return ids


async def create_keywords(session, adgroup_id):
    """Returns list of keyword ids."""
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
    items = [{"KeywordOrPhrase": k, "AdGroupId": adgroup_id, "Bid": 5_000_000}  # 5 ₽ initial
             for k in keywords]
    payload = {"method": "add", "params": {"Keywords": items}}
    data = await call(session, "keywords", payload)
    print("KEYWORDS:", json.dumps(data, ensure_ascii=False)[:500])
    ids = []
    for res in data.get("result", {}).get("AddResults", []):
        if res.get("Errors"):
            print("KW ERR:", res["Errors"])
        elif res.get("Id"):
            ids.append(res["Id"])
    return ids


async def suspend_campaign(session, cid):
    """Make sure it's suspended (default state is also suspended for new draft)."""
    payload = {"method": "suspend", "params": {"SelectionCriteria": {"Ids": [cid]}}}
    data = await call(session, "campaigns", payload)
    print("SUSPEND:", json.dumps(data, ensure_ascii=False)[:300])


async def main():
    async with aiohttp.ClientSession() as s:
        cid = await create_campaign(s)
        if not cid:
            print("STOP: campaign creation failed"); return
        agid = await create_adgroup(s, cid)
        if not agid:
            print("STOP: adgroup failed"); return
        ad_ids = await create_ads(s, agid)
        kw_ids = await create_keywords(s, agid)
        await suspend_campaign(s, cid)
        print()
        print("=" * 50)
        print(f"DONE. Campaign id: {cid}")
        print(f"AdGroup id: {agid}")
        print(f"Ads created: {len(ad_ids)}")
        print(f"Keywords: {len(kw_ids)}")
        print(f"State: SUSPENDED (manual activation needed in UI)")
        print(f"Daily budget: {DAILY_BUDGET_RUB} ₽")
        print(f"URL: https://direct.yandex.ru/?ulogin=&cmd=showCamp&cid={cid}")


if __name__ == "__main__":
    asyncio.run(main())
