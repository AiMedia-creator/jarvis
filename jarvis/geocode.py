"""Ortssuche für die Weltansicht über OpenStreetMap Nominatim – nach deren Nutzungsregeln.

Eindeutige Kennung (User-Agent), höchstens 1 Anfrage pro Sekunde, Zwischenspeicher, Suche nur auf ausdrücklichen
Wunsch (Enter/Sprachbefehl, nie bei jedem Tastendruck). Gesendet wird nur der Suchbegriff.
"""

from __future__ import annotations

import threading
import time

import requests

URL = "https://nominatim.openstreetmap.org/search"
HEADERS = {"User-Agent": "Jarvis-Assistent/1.0 (privater lokaler Assistent)", "Accept-Language": "de"}
_lock = threading.Lock()
_last = 0.0
_cache: dict[str, dict] = {}


def search(query: str) -> dict:
    global _last
    q = " ".join(str(query or "").split())[:120]
    if len(q) < 2:
        return {"status": "fehler", "hinweis": "Suchbegriff zu kurz."}
    key = q.lower()
    if key in _cache:
        return _cache[key]
    with _lock:
        wait = 1.05 - (time.monotonic() - _last)
        if wait > 0:
            time.sleep(wait)
        _last = time.monotonic()
        try:
            r = requests.get(URL, params={"q": q, "format": "jsonv2", "limit": 1}, headers=HEADERS, timeout=8)
            r.raise_for_status()
            hits = r.json()
        except (requests.RequestException, ValueError) as exc:
            return {"status": "fehler", "hinweis": f"Ortssuche nicht erreichbar ({type(exc).__name__})."}
    if not hits:
        result = {"status": "leer", "hinweis": f"Keinen Ort zu \"{q}\" gefunden."}
    else:
        h = hits[0]
        result = {"status": "ok", "treffer": {k: h.get(k) for k in ("lat", "lon", "display_name", "type", "addresstype")},
                  "quelle": "© OpenStreetMap-Mitwirkende (Nominatim)"}
    _cache[key] = result
    if len(_cache) > 500:
        _cache.pop(next(iter(_cache)))
    return result
