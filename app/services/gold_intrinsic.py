"""Intrinsic value of the gold deposit certificates, and each fund's intrinsic
bubble and implied dollar built from them.

Ported from the legacy pipeline's `calculute_bubble()` (gold_2.py), simplified
with the user on 2026-09-16:

- one dollar -- the market table's `dollar` row -- instead of several
- only the two certificates the funds actually hold are valued:

    mesghal       = ons x dollar / 9.5742                      (IRR)
    gram 18k      = mesghal / 4.6083 / 705 x 750
    gram 24k      = gram 18k x 4/3
    coin cert     = ons x dollar / 4.2492                      (IRR, one Emami coin)
    bar cert      = gram 24k / 10                              (IRR, one 100 mg unit)

  with ons in USD and dollar in IRR (the IRT dollar x 10), matching the IME
  certificate prices, which are IRR.

- certificate bubble        = price / intrinsic - 1
- certificate implied dollar = dollar x (1 + bubble)            (IRT)
- fund intrinsic bubble     = coin weight x coin-cert bubble + bar weight x bar-cert bubble
                              (cash and other holdings contribute nothing)
- fund implied dollar       = dollar x (1 + fund intrinsic bubble)   (IRT)

The legacy cross-fund coin/bar bubble decomposition and its several dollars
are intentionally not ported.
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


def certificate_intrinsics(ons_usd: float | None, dollar_irt: float | None) -> dict[str, float]:
    """IRR intrinsic value of one coin certificate and one bar-certificate unit."""
    if not ons_usd or not dollar_irt:
        return {}
    dollar_irr = dollar_irt * 10
    mesghal = ons_usd * dollar_irr / MESGHAL_DIVISOR
    gram18 = mesghal / MESGHAL_GRAMS / MESGHAL_FINENESS * GRAM18_FINENESS
    gram24 = gram18 * 4 / 3
    return {
        COIN_CERT: ons_usd * dollar_irr / COIN_DIVISOR,
        BAR_CERT: gram24 / BAR_UNITS_PER_GRAM,
    }


def apply_certificate_intrinsics(rows: list) -> dict[str, float]:
    """Fill intrinsic / bubble / implied_dollar on the two certificate rows of
    the market table, in place. Returns {certificate symbol: bubble}."""
    by_symbol = {r.symbol: r for r in rows}
    ons, dollar = by_symbol.get("ons"), by_symbol.get("dollar")
    values = certificate_intrinsics(ons.price if ons else None, dollar.price if dollar else None)
    bubbles: dict[str, float] = {}
    for symbol, intrinsic in values.items():
        row = by_symbol.get(symbol)
        if row is None or not row.price or not intrinsic:
            continue
        bubble = row.price / intrinsic - 1
        row.intrinsic = intrinsic
        row.bubble = bubble
        row.implied_dollar = dollar.price * (1 + bubble)
        bubbles[symbol] = bubble
    return bubbles


def fund_intrinsic(
    weights: dict[str, float] | None,
    cert_bubbles: dict[str, float],
    dollar_irt: float | None,
) -> tuple[float | None, float | None]:
    """(intrinsic bubble, implied dollar in IRT) for one fund. None when the
    fund has no weights or a certificate it holds has no bubble."""
    if not weights or not dollar_irt:
        return None, None
    coin_w = weights.get("sekke_weight") or 0
    bar_w = weights.get("shemsh_weight") or 0
    if (coin_w and COIN_CERT not in cert_bubbles) or (bar_w and BAR_CERT not in cert_bubbles):
        return None, None
    bubble = coin_w * cert_bubbles.get(COIN_CERT, 0) + bar_w * cert_bubbles.get(BAR_CERT, 0)
    return bubble, dollar_irt * (1 + bubble)
