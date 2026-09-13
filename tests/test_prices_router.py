def test_list_prices_returns_all_points(client):
    resp = client.get("/v1/prices")
    assert resp.status_code == 200
    isins = {p["isin"] for p in resp.json()}
    assert isins == {"geram18", "USDTTMN"}


def test_list_prices_filters_by_source(client):
    resp = client.get("/v1/prices", params={"source": "wallex"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["isin"] == "USDTTMN"


def test_get_price_returns_every_source_for_that_isin(client):
    resp = client.get("/v1/prices/geram18")
    assert resp.status_code == 200
    sources = {p["source"] for p in resp.json()}
    assert sources == {"estjt", "tabdeal"}


def test_get_price_404s_for_unknown_isin(client):
    resp = client.get("/v1/prices/NOPE")
    assert resp.status_code == 404


def test_price_point_shape(client):
    resp = client.get("/v1/prices/USDTTMN")
    assert resp.status_code == 200
    point = resp.json()[0]
    assert point == {
        "isin": "USDTTMN",
        "source": "wallex",
        "price": 233000.0,
        "updated_at": "2026-09-13T12:00:00Z",
        "payload": {},
    }
