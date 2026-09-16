import datetime as dt

import pytest

from app.schemas import GoldMarketRow
from app.services.gold_intrinsic import (
    apply_certificate_intrinsics,
    certificate_intrinsics,
    fund_intrinsic,
)

NOW = dt.datetime(2026, 9, 16, 12, 0, tzinfo=dt.timezone.utc)


def _row(symbol, price, unit):
    return GoldMarketRow(symbol=symbol, price=price, unit=unit, source="t", updated_at=NOW)


def test_certificate_intrinsics_follow_the_legacy_formulas():
    ons, dollar_irt = 4343.0, 230100.0
    values = certificate_intrinsics(ons, dollar_irt)

    dollar_irr = dollar_irt * 10
    mesghal = ons * dollar_irr / 9.5742
    gram24 = mesghal / 4.6083 / 705 * 750 * 4 / 3
    assert values["govahi_sekke"] == pytest.approx(ons * dollar_irr / 4.2492)
    assert values["govahi_shemsh"] == pytest.approx(gram24 / 10)
    # sanity: 18k gram in toman lands near the real ~23.5M market price
    assert 20e6 < gram24 * 3 / 4 / 10 < 28e6


def test_no_ons_or_dollar_means_no_intrinsics():
    assert certificate_intrinsics(None, 230100) == {}
    assert certificate_intrinsics(4343, 0) == {}


def test_certificate_rows_get_intrinsic_bubble_and_implied_dollar():
    rows = [
        _row("ons", 4343.0, "USD"),
        _row("dollar", 230100.0, "IRT"),
        _row("govahi_sekke", 2_310_000_000.0, "IRR"),
        _row("govahi_shemsh", 30_975_060.0, "IRR"),
        _row("geram18", 23_454_500.0, "IRT"),
    ]
    bubbles = apply_certificate_intrinsics(rows)
    coin, bar, gold18 = rows[2], rows[3], rows[4]

    assert coin.bubble == pytest.approx(coin.price / coin.intrinsic - 1)
    assert coin.implied_dollar == pytest.approx(230100.0 * (1 + coin.bubble))
    assert bar.bubble == pytest.approx(bar.price / bar.intrinsic - 1)
    assert {"govahi_sekke", "govahi_shemsh", "geram18"} <= set(bubbles)
    # an IRT row gets its intrinsic in toman: the legacy gold_18 formula / 10
    mesghal_irr = 4343.0 * 230100.0 * 10 / 9.5742
    assert gold18.intrinsic == pytest.approx(mesghal_irr / 4.6083 / 705 * 750 / 10)
    assert gold18.bubble == pytest.approx(gold18.price / gold18.intrinsic - 1)


def test_coins_mesghal_and_fractions_are_valued_in_their_own_unit():
    rows = [
        _row("ons", 4343.0, "USD"),
        _row("dollar", 230100.0, "IRT"),
        _row("sekee", 232_800_000.0, "IRT"),
        _row("sekee_bahar", 230_500_000.0, "IRT"),
        _row("nim", 118_900_000.0, "IRT"),
        _row("rob", 62_600_000.0, "IRT"),
        _row("mesghal", 101_800_000.0, "IRT"),
        _row("govahi_sekke", 2_310_000_000.0, "IRR"),
    ]
    apply_certificate_intrinsics(rows)
    m = {r.symbol: r for r in rows}

    coin_irr = 4343.0 * 2_301_000.0 / 4.2492
    assert m["sekee"].intrinsic == pytest.approx(coin_irr / 10)             # toman
    assert m["govahi_sekke"].intrinsic == pytest.approx(coin_irr)           # rial, same coin
    assert m["sekee_bahar"].intrinsic == pytest.approx(m["sekee"].intrinsic)
    assert m["nim"].intrinsic == pytest.approx(m["sekee"].intrinsic / 2)
    assert m["rob"].intrinsic == pytest.approx(m["sekee"].intrinsic / 4)
    assert m["mesghal"].intrinsic == pytest.approx(4343.0 * 2_301_000.0 / 9.5742 / 10)
    for r in rows[2:]:
        assert r.implied_dollar == pytest.approx(230100.0 * (1 + r.bubble))


def test_fund_intrinsic_bubble_is_nominal_plus_weighted_certificate_bubbles():
    bubbles = {"govahi_sekke": -0.02, "govahi_shemsh": -0.04}
    weights = {"sekke_weight": 0.1, "shemsh_weight": 0.85, "cash_weight": 0.05}

    bubble, implied = fund_intrinsic(weights, bubbles, 230000.0, 0.015)

    assert bubble == pytest.approx(0.015 + 0.1 * -0.02 + 0.85 * -0.04)
    assert implied == pytest.approx(230000.0 * (1 + bubble))


def test_fund_without_weights_or_needed_bubble_gets_none():
    assert fund_intrinsic(None, {"govahi_sekke": 0.0, "govahi_shemsh": 0.0}, 230000.0, 0.0) == (None, None)
    assert fund_intrinsic({"sekke_weight": 0.2, "shemsh_weight": 0.8}, {"govahi_shemsh": -0.01}, 230000.0, 0.0) == (None, None)
    assert fund_intrinsic({"sekke_weight": 0.0, "shemsh_weight": 1.0}, {"govahi_shemsh": -0.01}, 230000.0, None) == (None, None)
    # a pure-bar fund doesn't need the coin bubble
    b, _ = fund_intrinsic({"sekke_weight": 0.0, "shemsh_weight": 1.0}, {"govahi_shemsh": -0.01}, 230000.0, 0.0)
    assert b == pytest.approx(-0.01)
