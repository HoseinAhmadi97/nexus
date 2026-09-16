"""Intrinsic value of the gold instruments, and each fund's intrinsic bubble
and implied dollar built from the two deposit certificates.

Ported from the legacy pipeline's `calculute_bubble()` (gold_2.py), simplified
with the user on 2026-09-16 to one dollar -- the market table's `dollar` row --
instead of several. All values below are IRR, with ons in USD and dollar in IRR
(the IRT dollar x 10):

    mesghal     = ons x dollar / 9.5742               (legacy: mesghal)
    gram 18k    = mesghal / 4.6083 / 705 x 750        (legacy: gold_18)
    gram 24k    = gram 18k x 4/3                      (legacy: gold_24)
    Emami coin  = ons x dollar / 4.2492               (legacy: sekke)
    coin cert   = Emami coin                          (legacy id 42)
    bar cert    = gram 24k / 10                       (legacy id 41, one 100 mg unit)

Not in the legacy code, derived from the Emami coin because they hold the same
gold (8.133 g at 900 fineness per full coin):

    Bahar Azadi coin = Emami coin;  half coin = Emami / 2;  quarter coin = Emami / 4

Per instrument: bubble = price / intrinsic - 1, implied dollar (IRT) = dollar x
(1 + bubble). Per fund (legacy `bubble` column): intrinsic bubble = nominal bubble
(last trade / NAV - 1) + coin weight x coin-cert bubble + bar weight x bar-cert
bubble (cash and other holdings contribute nothing), implied dollar = dollar x
(1 + that).

The legacy cross-fund coin/bar bubble decomposition and its several dollars are
intentionally not ported.
"""
from __future__ import annotations

MESGHAL_DIVISOR = 9.5742
MESGHAL_GRAMS = 4.6083
MESGHAL_FINENESS = 705
GRAM18_FINENESS = 750
COIN_DIVISOR = 4.2492
BAR_UNITS_PER_GRAM = 10

COIN_CERT = "govahi_sekke"
BAR_CERT = "govahi_shemsh"


def intrinsic_values_irr(ons_usd: float | None, dollar_irt: float | None) -> dict[str, float]:
    """IRR intrinsic value per market symbol (the table's own unit per row is
    applied later). Empty without ons or dollar."""
    if not ons_usd or not dollar_irt:
        return {}
    dollar_irr = dollar_irt * 10
    mesghal = ons_usd * dollar_irr / MESGHAL_DIVISOR
    gram18 = mesghal / MESGHAL_GRAMS / MESGHAL_FINENESS * GRAM18_FINENESS
    gram24 = gram18 * 4 / 3
    coin = ons_usd * dollar_irr / COIN_DIVISOR
    return {
        "mesghal": mesghal,
        "geram18": gram18,
        "geram24": gram24,
        "sekee": coin,
        "sekee_bahar": coin,
        "nim": coin / 2,
        "rob": coin / 4,
        COIN_CERT: coin,
        BAR_CERT: gram24 / BAR_UNITS_PER_GRAM,
    }


# kept for the tests and callers that only want the two certificates
def certificate_intrinsics(ons_usd: float | None, dollar_irt: float | None) -> dict[str, float]:
    values = intrinsic_values_irr(ons_usd, dollar_irt)
    return {k: values[k] for k in (COIN_CERT, BAR_CERT) if k in values}


def apply_intrinsics(rows: list) -> dict[str, float]:
    """Fill intrinsic (in the row's own unit) / bubble / implied_dollar on every
    market row that has an intrinsic value, in place. Returns {symbol: bubble}."""
    by_symbol = {r.symbol: r for r in rows}
    ons, dollar = by_symbol.get("ons"), by_symbol.get("dollar")
    values = intrinsic_values_irr(ons.price if ons else None, dollar.price if dollar else None)
    bubbles: dict[str, float] = {}
    for symbol, irr in values.items():
        row = by_symbol.get(symbol)
        if row is None or not row.price or not irr:
            continue
        intrinsic = irr / 10 if row.unit == "IRT" else irr
        bubble = row.price / intrinsic - 1
        row.intrinsic = intrinsic
        row.bubble = bubble
        row.implied_dollar = dollar.price * (1 + bubble)
        bubbles[symbol] = bubble
    return bubbles


# the market builder's original name
apply_certificate_intrinsics = apply_intrinsics


def fund_intrinsic(
    weights: dict[str, float] | None,
    cert_bubbles: dict[str, float],
    dollar_irt: float | None,
    nominal_bubble: float | None,
) -> tuple[float | None, float | None]:
    """(intrinsic bubble, implied dollar in IRT) for one fund: the fund's own
    premium over NAV plus the premium of the certificates inside that NAV.
    None without weights, a nominal bubble, or a needed certificate bubble."""
    if not weights or not dollar_irt or nominal_bubble is None:
        return None, None
    coin_w = weights.get("sekke_weight") or 0
    bar_w = weights.get("shemsh_weight") or 0
    if (coin_w and COIN_CERT not in cert_bubbles) or (bar_w and BAR_CERT not in cert_bubbles):
        return None, None
    bubble = nominal_bubble + coin_w * cert_bubbles.get(COIN_CERT, 0) + bar_w * cert_bubbles.get(BAR_CERT, 0)
    return bubble, dollar_irt * (1 + bubble)
