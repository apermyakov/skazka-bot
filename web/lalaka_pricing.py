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
# Re-tiered 2026-06-07 to $19.99 equivalent — international families pay
# premium for personalised audio storybook; RUB charge updated for ЮKassa.
PRICES: dict[str, tuple[str, float, str, int]] = {
    "en":    ("USD",  19.99,  "$19.99",     2000),
    "de":    ("EUR",  18.99,  "18,99 €",    2100),
    "es":    ("EUR",  18.99,  "18,99 €",    2100),
    "fr":    ("EUR",  18.99,  "18,99 €",    2100),
    "it":    ("EUR",  18.99,  "18,99 €",    2100),
    "pl":    ("PLN",  79.00,  "79 zł",      2000),
    "pt-BR": ("BRL",  99.00,  "R$ 99",      2000),
    "tr":    ("TRY", 699.00,  "₺699",       2200),
    "ja":    ("JPY", 2990,    "¥2,990",     2000),
    "ko":    ("KRW", 26900,   "₩26,900",    2000),
    "ar":    ("USD",  19.99,  "$19.99",     2000),  # KSA/UAE — USD widely accepted
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
