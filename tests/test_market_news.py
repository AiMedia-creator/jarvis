"""Kursdaten-Anbieter, Ortszuordnung von Meldungen und die Bridge-Endpunkte fuer die adaptive Oberflaeche."""

import datetime

import pytest
from aiohttp.test_utils import TestClient, TestServer

from jarvis import geo, market, v3_bridge

TOKEN = "offline-unit-test-bridge-credential-only"


@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    for var in ("JARVIS_MARKET_PROVIDER", "ALPHAVANTAGE_API_KEY", "JARVIS_ALPHAVANTAGE_API_KEY", "JARVIS_DEMO"):
        monkeypatch.delenv(var, raising=False)
    market._cache.clear()


def test_aufloesung_eindeutig_mehrdeutig_unbekannt():
    assert market.resolve("Apple")["security"]["symbol"] == "AAPL"
    assert market.resolve("tsla")["security"]["symbol"] == "TSLA"
    sap = market.resolve("SAP")  # Xetra oder NYSE-ADR: Rueckfrage statt Raten
    assert sap["status"] == "mehrdeutig" and {o["symbol"] for o in sap["optionen"]} == {"SAP", "SAP.DEX"}
    assert market.resolve("SAP.DEX")["security"]["currency"] == "EUR"
    assert market.resolve("NVDA")["security"]["name"] == "NVIDIA Corp."
    assert market.resolve("Siemens")["security"]["currency"] == "EUR"
    assert market.resolve("Kuchenfabrik")["status"] == "unbekannt"
    assert market.resolve("")["status"] == "unbekannt"


def test_mehrdeutiger_name_fuehrt_zu_rueckfrage(monkeypatch):
    monkeypatch.setattr(market, "DIRECTORY", market.DIRECTORY + [
        {"symbol": "APLE", "name": "Apple Hospitality REIT", "aliases": ["apple"], "currency": "USD", "exchange": "NYSE"}])
    out = market.resolve("apple")
    assert out["status"] == "mehrdeutig" and {o["symbol"] for o in out["optionen"]} == {"AAPL", "APLE"}


def test_ohne_quelle_keine_kurse():
    out = market.quote_series("Apple")
    assert out["status"] == "nicht_eingerichtet" and "punkte" not in out
    assert out["security"]["symbol"] == "AAPL"


def test_demo_ist_gekennzeichnet(monkeypatch):
    monkeypatch.setenv("JARVIS_MARKET_PROVIDER", "demo")
    out = market.quote_series("Tesla")
    assert out["status"] == "ok" and out["demo"] is True and "DEMO" in out["quelle"]
    assert len(out["punkte"]) >= 60 and out["zeitraum"]["von"] < out["zeitraum"]["bis"]


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


def test_alpha_vantage_echte_struktur_und_limit(monkeypatch):
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "test-key")
    calls = []
    payload = {"Meta Data": {"3. Last Refreshed": "2026-10-16", "5. Time Zone": "US/Eastern"},
               "Time Series (Daily)": {"2026-10-16": {"4. close": "251.10"}, "2026-10-15": {"4. close": "249.00"}}}

    def fake_get(url, params, timeout):
        calls.append(params)
        return FakeResponse(payload)

    monkeypatch.setattr(market.requests, "get", fake_get)
    out = market.quote_series("Apple")
    assert out["status"] == "ok" and out["demo"] is False and out["quelle"].startswith("Alpha Vantage")
    assert out["punkte"] == [{"t": "2026-10-15", "c": 249.0}, {"t": "2026-10-16", "c": 251.1}]
    assert out["stand"] == "2026-10-16 (US/Eastern)" and out["security"]["currency"] == "USD"
    assert calls[0]["symbol"] == "AAPL" and calls[0]["apikey"] == "test-key"
    market.quote_series("Apple")
    assert len(calls) == 1  # Cache schont das Abruflimit
    market._cache.clear()
    payload.clear()
    payload["Note"] = "rate limit"
    out = market.quote_series("Apple")
    assert out["status"] == "fehler" and "Abruflimit" in out["hinweis"] and "punkte" not in out


def test_orte_nur_bei_eindeutiger_nennung():
    assert geo.locate("Proteste in Paris gehen weiter")["name"] == "Paris"
    assert geo.locate("Ukraine meldet Angriffe auf Kiew")["name"] == "Kyjiw"  # Stadt vor Land
    assert geo.locate("Regierung in der Ukraine")["name"] == "Ukraine (Kyjiw)"
    assert geo.locate("Parisienne-Gebäck wird teurer") is None
    assert geo.locate("Zinsentscheid erwartet") is None


@pytest.mark.asyncio
async def test_bridge_nachrichten_und_kurse(monkeypatch):
    from jarvis.tools import news

    when = datetime.datetime(2026, 10, 17, 8, 30, tzinfo=datetime.timezone.utc)
    monkeypatch.setattr(news, "fetch_items", lambda rubrik, stichwort, anzahl: ([
        {"quelle": "Tagesschau", "titel": "Gipfel in Brüssel", "text": "EU berät", "zeit": when, "link": "https://x"},
        {"quelle": "Spiegel", "titel": "Zinsen bleiben unverändert", "text": "", "zeit": None, "link": ""},
        {"quelle": "Tagesschau", "titel": "tagesschau", "text": "", "zeit": None, "link": ""}], []))
    client = TestClient(TestServer(v3_bridge.create_app(TOKEN, demo=True)))
    await client.start_server()
    auth = {"Authorization": "Bearer " + TOKEN}
    try:
        assert (await client.get("/internal/news")).status == 401
        data = await (await client.get("/internal/news?rubrik=allgemein", headers=auth)).json()
        assert data["status"] == "ok" and data["meldungen"][0]["ort"]["name"] == "Brüssel"
        assert data["meldungen"][1]["ort"] is None and data["meldungen"][0]["zeit"].startswith("2026-10-17")
        assert len(data["meldungen"]) == 2  # Feed-Kopfzeile "tagesschau" ist keine Meldung
        names = await (await client.get("/internal/market?verzeichnis=1", headers=auth)).json()
        assert any(d["symbol"] == "AAPL" for d in names["verzeichnis"])
        quote = await (await client.get("/internal/market?q=Apple", headers=auth)).json()
        assert quote["status"] == "nicht_eingerichtet"
    finally:
        await client.close()
