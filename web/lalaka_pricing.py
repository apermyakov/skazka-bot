# -*- coding: utf-8 -*-
"""Per-locale pricing for Lalaka. ЮKassa accepts only RUB, so we display the
local-currency price and charge an equivalent RUB amount calculated at fixed
conservative rates baked into PRICES below.

Why fixed (not live FX): live conversion adds an external dependency and a tiny
loss-leader risk on every price tick. For v1 the spread on the conservative side
(~3-5% margin built in) covers fluctuation. Re-tune the table quarterly.

display_amount   — number shown to the user, in `currency`
display_str      — pre-formatted with the locale's currency symbol/separator
rub_amount       — what ЮKassa actually charges
"""

# locale → (currency_code, display_amount_local, display_string, rub_amount)
PRICES: dict[str, tuple[str, float, str, int]] = {
    "en":    ("USD",   9.99,  "$9.99",      1000),
    "de":    ("EUR",   9.99,  "9,99 €",     1050),
    "es":    ("EUR",   9.99,  "9,99 €",     1050),
    "fr":    ("EUR",   9.99,  "9,99 €",     1050),
    "it":    ("EUR",   9.99,  "9,99 €",     1050),
    "pl":    ("PLN",  39.00,  "39 zł",      1000),
    "pt-BR": ("BRL",  49.00,  "R$ 49",      1000),
    "tr":    ("TRY", 349.00,  "₺349",       1100),
    "ja":    ("JPY", 1490,    "¥1,490",     1000),
    "ko":    ("KRW", 13900,   "₩13,900",    1000),
    "ar":    ("USD",   9.99,  "$9.99",      1000),  # SAR has no symbol consensus; use USD for KSA/UAE
    "ru":    ("RUB",   599,   "599 ₽",       599),  # native — pass through
    "uk":    ("UAH",  399,    "₴399",       1100),
}

DEFAULT_LOCALE = "en"


def price_for(locale: str) -> dict:
    """Return pricing info for a locale. Falls back to default (USD)."""
    cur, amt, disp, rub = PRICES.get(locale, PRICES[DEFAULT_LOCALE])
    return {
        "currency": cur,
        "display_amount": amt,
        "display": disp,
        "amount_rub": rub,
    }


def all_prices() -> dict[str, dict]:
    """For admin debugging."""
    return {loc: price_for(loc) for loc in PRICES}
