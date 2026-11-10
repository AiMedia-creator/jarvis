"""Kursdaten fuer die Chartansicht – austauschbare Anbieter, Schluessel nur im Backend, keine erfundenen Kurse.

Anbieter (JARVIS_MARKET_PROVIDER):
- "alphavantage": offizielle Alpha-Vantage-API, Schluessel ALPHAVANTAGE_API_KEY (Tagesschlusskurse, ca. 100 Handelstage).
- "demo": ausdruecklich gekennzeichnete, synthetische Demo-Daten (nie als echte Kurse ausgegeben).
- leer: Alpha Vantage, falls ein Schluessel gesetzt ist; sonst Status "nicht_eingerichtet".
Keine Orderausfuehrung, keine Handelsfunktionen.
"""

from __future__ import annotations

import datetime
import math
import re
import threading
import time
from dataclasses import dataclass
from typing import Protocol

import requests

from . import config

# Kleines Verzeichnis fuer die Namensaufloesung. Mehrdeutige Namen fuehren zu einer Rueckfrage statt eines Charts.
DIRECTORY: list[dict] = [
    {"symbol": "AAPL", "name": "Apple Inc.", "aliases": ["apple"], "currency": "USD", "exchange": "NASDAQ"},
    {"symbol": "MSFT", "name": "Microsoft Corp.", "aliases": ["microsoft"], "currency": "USD", "exchange": "NASDAQ"},
    {"symbol": "NVDA", "name": "NVIDIA Corp.", "aliases": ["nvidia"], "currency": "USD", "exchange": "NASDAQ"},
    {"symbol": "TSLA", "name": "Tesla Inc.", "aliases": ["tesla"], "currency": "USD", "exchange": "NASDAQ"},
    {"symbol": "AMZN", "name": "Amazon.com Inc.", "aliases": ["amazon"], "currency": "USD", "exchange": "NASDAQ"},
    {"symbol": "GOOGL", "name": "Alphabet Inc. (Klasse A)", "aliases": ["alphabet", "google"], "currency": "USD", "exchange": "NASDAQ"},
    {"symbol": "META", "name": "Meta Platforms Inc.", "aliases": ["meta", "facebook"], "currency": "USD", "exchange": "NASDAQ"},
    {"symbol": "NFLX", "name": "Netflix Inc.", "aliases": ["netflix"], "currency": "USD", "exchange": "NASDAQ"},
    {"symbol": "AMD", "name": "Advanced Micro Devices", "aliases": ["amd"], "currency": "USD", "exchange": "NASDAQ"},
    {"symbol": "INTC", "name": "Intel Corp.", "aliases": ["intel"], "currency": "USD", "exchange": "NASDAQ"},
    {"symbol": "IBM", "name": "IBM", "aliases": ["ibm"], "currency": "USD", "exchange": "NYSE"},
    {"symbol": "KO", "name": "Coca-Cola Co.", "aliases": ["coca-cola", "coca cola"], "currency": "USD", "exchange": "NYSE"},
    {"symbol": "DIS", "name": "Walt Disney Co.", "aliases": ["disney"], "currency": "USD", "exchange": "NYSE"},
    {"symbol": "SAP.DEX", "name": "SAP SE (Xetra)", "aliases": ["sap"], "currency": "EUR", "exchange": "XETRA"},
    {"symbol": "SAP", "name": "SAP SE (ADR, NYSE)", "aliases": ["sap"], "currency": "USD", "exchange": "NYSE"},
    {"symbol": "SIE.DEX", "name": "Siemens AG (Xetra)", "aliases": ["siemens"], "currency": "EUR", "exchange": "XETRA"},
    {"symbol": "ALV.DEX", "name": "Allianz SE (Xetra)", "aliases": ["allianz"], "currency": "EUR", "exchange": "XETRA"},
    {"symbol": "BMW.DEX", "name": "BMW AG (Xetra)", "aliases": ["bmw"], "currency": "EUR", "exchange": "XETRA"},
    {"symbol": "MBG.DEX", "name": "Mercedes-Benz Group (Xetra)", "aliases": ["mercedes", "mercedes-benz"], "currency": "EUR", "exchange": "XETRA"},
    {"symbol": "VOW3.DEX", "name": "Volkswagen AG Vz. (Xetra)", "aliases": ["volkswagen", "vw"], "currency": "EUR", "exchange": "XETRA"},
    {"symbol": "DTE.DEX", "name": "Deutsche Telekom (Xetra)", "aliases": ["telekom", "deutsche telekom"], "currency": "EUR", "exchange": "XETRA"},
    {"symbol": "ADS.DEX", "name": "adidas AG (Xetra)", "aliases": ["adidas"], "currency": "EUR", "exchange": "XETRA"},
]
_TICKER = re.compile(r"^[A-Z][A-Z0-9]{0,5}(?:\.[A-Z]{2,4})?$")


def directory() -> list[dict]:
    """Namen fuer die Absichtserkennung im Frontend (keine Kurse)."""
    return [{"symbol": d["symbol"], "name": d["name"], "aliases": d["aliases"]} for d in DIRECTORY]


def resolve(query: str) -> dict:
    """Name oder Symbol -> genau ein Wertpapier, eine Rueckfrage (mehrdeutig) oder unbekannt."""
    raw = (query or "").strip()
    if not raw or len(raw) > 60:
        return {"status": "unbekannt"}
    # Namen zuerst: "SAP" kann Xetra (EUR) oder NYSE-ADR (USD) sein -> Rueckfrage statt falschem Chart.
    hits = [d for d in DIRECTORY if raw.lower() in d["aliases"]]
    if len(hits) == 1:
        return {"status": "ok", "security": hits[0]}
    if len(hits) > 1:
        return {"status": "mehrdeutig", "optionen": [{"symbol": d["symbol"], "name": d["name"]} for d in hits]}
    by_symbol = [d for d in DIRECTORY if d["symbol"] == raw.upper()]
    if by_symbol:
        return {"status": "ok", "security": by_symbol[0]}
    if _TICKER.fullmatch(raw):  # explizit genanntes Tickersymbol, Waehrung unbekannt
        return {"status": "ok", "security": {"symbol": raw, "name": raw, "currency": None, "exchange": None}}
    return {"status": "unbekannt"}


@dataclass
class Series:
    points: list[tuple[str, float]]  # (ISO-Datum, Schlusskurs), aufsteigend
    as_of: str
    source: str
    demo: bool = False


class Provider(Protocol):
    name: str

    def daily(self, symbol: str) -> Series: ...


class ProviderError(Exception):
    """Anbieter liefert keine verwertbaren Daten (Limit, unbekanntes Symbol, Netzwerk)."""


class AlphaVantage:
    name = "Alpha Vantage"
    URL = "https://www.alphavantage.co/query"

    def __init__(self, key: str):
        self.key = key

    def daily(self, symbol: str) -> Series:
        try:
            r = requests.get(self.URL, params={"function": "TIME_SERIES_DAILY", "symbol": symbol,
                                               "outputsize": "compact", "apikey": self.key}, timeout=15)
            r.raise_for_status()
            data = r.json()
        except (requests.RequestException, ValueError) as exc:
            raise ProviderError(f"Alpha Vantage nicht erreichbar ({type(exc).__name__}).") from exc
        if "Note" in data or "Information" in data:
            raise ProviderError("Alpha Vantage: Abruflimit erreicht oder Zugang eingeschränkt.")
        if "Error Message" in data:
            raise ProviderError(f"Alpha Vantage kennt das Symbol {symbol} nicht.")
        series = data.get("Time Series (Daily)") or {}
        points = []
        for day, row in series.items():
            try:
                points.append((day, float(row["4. close"])))
            except (KeyError, TypeError, ValueError):
                continue
        if not points:
            raise ProviderError("Alpha Vantage lieferte keine Kurse.")
        points.sort()
        meta = data.get("Meta Data") or {}
        as_of = f"{meta.get('3. Last Refreshed', points[-1][0])} ({meta.get('5. Time Zone', 'Börsenzeit')})"
        return Series(points, as_of, "Alpha Vantage (Tagesschlusskurse)")


class DemoProvider:
    name = "Demo"

    def daily(self, symbol: str) -> Series:
        seed = sum(ord(c) for c in symbol)
        today = datetime.date.today()
        days = [today - datetime.timedelta(days=i) for i in range(140)]
        days = [d for d in reversed(days) if d.weekday() < 5][-100:]
        base = 50 + seed % 250
        points = [(d.isoformat(), round(base * (1 + 0.08 * math.sin((i + seed) / 9) + 0.002 * i), 2))
                  for i, d in enumerate(days)]
        return Series(points, f"{days[-1].isoformat()} (synthetisch)", "DEMO – synthetische Daten, keine echten Kurse",
                      demo=True)


def provider() -> Provider | None:
    choice = (config.get("market_provider") or "").strip().lower()
    key = config.get("alphavantage_api_key") or ""
    if choice == "demo":
        return DemoProvider()
    if choice in ("", "alphavantage") and key:
        return AlphaVantage(key)
    if choice == "" and str(config.get("demo", "false")).lower() in {"true", "1", "yes"}:
        return DemoProvider()
    return None


_cache: dict[str, tuple[float, Series]] = {}
_cache_lock = threading.Lock()
CACHE_SECONDS = 15 * 60  # Tagesdaten; schont das Abruflimit


def quote_series(query: str) -> dict:
    """Antwort fuer /internal/market: aufgeloestes Wertpapier + Kursreihe, oder ein ehrlicher Status."""
    resolved = resolve(query)
    if resolved["status"] != "ok":
        return resolved
    security = resolved["security"]
    prov = provider()
    if prov is None:
        return {"status": "nicht_eingerichtet", "security": _public(security),
                "hinweis": "Keine Kursdatenquelle eingerichtet. ALPHAVANTAGE_API_KEY setzen "
                           "oder JARVIS_MARKET_PROVIDER=demo für gekennzeichnete Demo-Daten."}
    key = f"{prov.name}:{security['symbol']}"
    with _cache_lock:
        hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        series = hit[1]
    else:
        try:
            series = prov.daily(security["symbol"])
        except ProviderError as exc:
            return {"status": "fehler", "security": _public(security), "hinweis": str(exc), "quelle": prov.name}
        with _cache_lock:
            _cache[key] = (time.time(), series)
    return {"status": "ok", "security": _public(security), "quelle": series.source, "stand": series.as_of,
            "demo": series.demo, "punkte": [{"t": t, "c": c} for t, c in series.points],
            "zeitraum": {"von": series.points[0][0], "bis": series.points[-1][0]}}


def _public(security: dict) -> dict:
    return {k: security.get(k) for k in ("symbol", "name", "currency", "exchange")}
