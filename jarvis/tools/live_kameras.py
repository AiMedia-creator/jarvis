"""Live-Kameras: oeffentliche Live-Webcams einer Stadt zeigen ("Zeig mir, was gerade in Tel Aviv los ist").

Idee und Grundkatalog aus World Monitor (github.com/koala73/worldmonitor, AGPL-3.0; uebernommen sind nur die
oeffentlichen Stream-Adressen als Daten, kein Code). Quellen sind oeffentliche Panorama- und Stadt-Webcams von
Sendern und Webcam-Betreibern auf YouTube – keine privaten Ueberwachungskameras.

Ablauf je Anfrage:
1. Ort im Katalog suchen (deutsche und englische Namen); jede Quelle wird live geprueft (isLiveNow, einbettbar),
   Ergebnis 15 Minuten zwischengespeichert. Livestreams enden oft – darum nie blind anzeigen.
2. Nichts live? Suche nach "<Ort> live cam": mit ``youtube_api_key`` ueber die offizielle YouTube-Data-API, sonst
   ueber die oeffentliche YouTube-Suche (abschaltbar: ``"kamera_suche": "aus"``).
3. Optional Windy-Webcams (``windy_api_key``, kostenlos unter api.windy.com) – weltweit, oft Standbild/Zeitraffer.
4. Gefundenes Bild erscheint als Kamera-Ansicht in der Oberflaeche (http://localhost:8765), Quelle und Hinweis dabei.
Eigene Kameras: ~/.jarvis/kameras_live.json im Format {"stadt": [{"video": "YouTube-ID", "titel": "…"}]}.
"""

from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
import urllib.parse

import requests

from .. import config
from . import Tool, ToolFailure, string

TIMEOUT = 12
CACHE_SECONDS = 15 * 60
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) "
                    "Version/17.0 Safari/605.1.15",
      "Accept-Language": "de-DE,de;q=0.9,en;q=0.8", "Cookie": "CONSENT=YES+1; SOCS=CAI"}

# Grundkatalog: World-Monitor-Webcams (Stand 08.11.2026, live geprueft) + Berlin + Tel Aviv-Ersatz.
CATALOG: dict[str, dict] = {
    "berlin": {"stadt": "Berlin", "land": "Deutschland", "aliase": ["berlin", "olympiastadion", "charlottenburg"],
               "quellen": [{"video": "tw9PIVnDbbA", "titel": "Panorama vom Corbusierhaus Berlin"},
                           {"video": "61b3Jty73Zo", "titel": "Blick vom Corbusierhaus auf das Olympiastadion"},
                           {"video": "v1LrNrEwWDA", "titel": "Berlin – Himmel über der Stadt"}]},
    "tel-aviv": {"stadt": "Tel Aviv", "land": "Israel", "aliase": ["tel aviv", "telaviv", "tel-aviv", "jaffa", "yafo", "israel"],
                 "hinweis": "Eine eigene Tel-Aviv-Kamera ist gerade nicht live; das sind mehrere Live-Kameras aus Israel "
                            "und Nahost.",
                 "quellen": [{"video": "fn_ZWTr0Tb8", "titel": "Live-Kameras aus Israel und Nahost"},
                             {"video": "q1NR5q9tZd0", "titel": "Echtzeit-Kameras aus Israel, Iran, Katar"},
                             {"video": "jC8_C4yGHas", "titel": "Mehrere Kameras in Israel und Nahost"}]},
    "jerusalem": {"stadt": "Jerusalem", "land": "Israel", "aliase": ["jerusalem", "klagemauer", "western wall"],
                  "quellen": [{"video": "zp6LNSoq000", "titel": "Klagemauer, Archäologischer Park"},
                              {"video": "77akujLn4k8", "titel": "EarthCam – Klagemauer"},
                              {"video": "LMHUcDktP-w", "titel": "Klagemauer – Hauptplatz"}]},
    "nahost": {"stadt": "Nahost", "land": "mehrere", "aliase": ["nahost", "naher osten", "middle east", "iran", "katar"],
               "quellen": [{"video": "fn_ZWTr0Tb8", "titel": "Live-Kameras aus Nahost"},
                           {"video": "AkqGOcpDvZU", "titel": "Echtzeit-Kameras aus Nahost"},
                           {"video": "q1NR5q9tZd0", "titel": "Echtzeit-Kameras aus Nahost"}]},
    "mekka": {"stadt": "Mekka", "land": "Saudi-Arabien", "aliase": ["mekka", "mecca", "makkah"],
              "quellen": [{"video": "eC4LfEVxvKg", "titel": "Mekka live"}]},
    "istanbul": {"stadt": "Istanbul", "land": "Türkei", "aliase": ["istanbul", "bosporus"],
                 "quellen": [{"video": "bbVe5h7X3uw", "titel": "Hagia Sophia und Bosporus"}]},
    "kiew": {"stadt": "Kiew", "land": "Ukraine", "aliase": ["kiew", "kyiv", "kiev", "ukraine", "odessa", "charkiw"],
             "quellen": [{"video": "e2gC37ILQmk", "titel": "Live-Kameras aus der Ukraine"},
                         {"video": "IcZ-7sFi1HM", "titel": "Skyline-Kameras aus der Ukraine"}]},
    "paris": {"stadt": "Paris", "land": "Frankreich", "aliase": ["paris", "montmartre"],
              "quellen": [{"video": "-xzg3wujOVM", "titel": "Sacré-Cœur de Montmartre"}]},
    "sankt-petersburg": {"stadt": "Sankt Petersburg", "land": "Russland",
                         "aliase": ["sankt petersburg", "st. petersburg", "st petersburg", "petersburg"],
                         "quellen": [{"video": "CjtIYbmVfck", "titel": "Palastbrücke an der Newa"}]},
    "london": {"stadt": "London", "land": "Großbritannien", "aliase": ["london", "abbey road"],
               "quellen": [{"video": "zMCea32gpmg", "titel": "EarthCam – Abbey Road"}]},
    "washington": {"stadt": "Washington", "land": "USA", "aliase": ["washington", "washington dc"],
                   "quellen": [{"video": "oDCAAfOSqvA", "titel": "EarthCam – Washington Monument"}]},
    "new-york": {"stadt": "New York", "land": "USA", "aliase": ["new york", "newyork", "nyc", "manhattan", "times square"],
                 "quellen": [{"video": "VGnFLdQW39A", "titel": "Times Square und Skyline"},
                             {"video": "JQ_jwk_7OVE", "titel": "EarthCam – Times Square"}]},
    "los-angeles": {"stadt": "Los Angeles", "land": "USA", "aliase": ["los angeles", "la", "venice beach"],
                    "quellen": [{"video": "EO_1LWqsCNE", "titel": "Venice Beach"}]},
    "miami": {"stadt": "Miami", "land": "USA", "aliase": ["miami", "miami beach"],
              "quellen": [{"video": "bi7B4EmyHHs", "titel": "Sunny Isles Beach"}, {"video": "WT69M210Z18", "titel": "South Beach"},
                          {"video": "4fzzp0XVPoA", "titel": "Hafen und Skyline"}]},
    "taipeh": {"stadt": "Taipeh", "land": "Taiwan", "aliase": ["taipeh", "taipei", "taiwan"],
               "quellen": [{"video": "z_fY1pj1VBw", "titel": "Blick vom Xiangshan auf Taipeh 101"}]},
    "shanghai": {"stadt": "Shanghai", "land": "China", "aliase": ["shanghai"],
                 "quellen": [{"video": "Z-g8M1QGKbg", "titel": "Skyline am Bund"}]},
    "tokio": {"stadt": "Tokio", "land": "Japan", "aliase": ["tokio", "tokyo"],
              "quellen": [{"video": "_k-5U7IeK8g", "titel": "Bucht von Tokio"}]},
    "seoul": {"stadt": "Seoul", "land": "Südkorea", "aliase": ["seoul", "südkorea", "suedkorea"],
              "quellen": [{"video": "vk5BHoDxXf0", "titel": "Han-Fluss und Skyline"}]},
    "sydney": {"stadt": "Sydney", "land": "Australien", "aliase": ["sydney"],
               "quellen": [{"video": "5uZa3-RMFos", "titel": "Hafen von Sydney"}]},
    "weltraum": {"stadt": "Erde aus dem All", "land": "ISS", "aliase": ["iss", "weltraum", "weltall", "erde von oben", "nasa"],
                 "quellen": [{"video": "M3HKLzjvKPc", "titel": "Live von der ISS (NASA)"},
                             {"video": "fO9e9jnhYK8", "titel": "Erde in 4K von der ISS"}]},
    "spacex": {"stadt": "SpaceX Starbase", "land": "USA", "aliase": ["spacex", "starbase", "starship"],
               "quellen": [{"video": "mhJRzQsLZGg", "titel": "Starbase live"}, {"video": "Jm8wRjD3xVA", "titel": "Space Coast live"}]},
}

_live_cache: dict[str, tuple[float, dict | None]] = {}
_cache_lock = threading.Lock()
_current: dict | None = None


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text).lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"\b(live|livecam|webcam|kamera|kameras|cam|jetzt|gerade|in|aus|von|der|die|das)\b", " ", text)
    return re.sub(r"[^a-z0-9 .-]+", " ", text).strip()


def _user_catalog() -> dict:
    try:
        data = json.loads((config.data_dir() / "kameras_live.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for city, sources in (data or {}).items():
        if isinstance(sources, list):
            out[_norm(city)] = {"stadt": str(city), "land": "", "aliase": [_norm(city)],
                                "quellen": [s for s in sources if isinstance(s, dict) and s.get("video")]}
    return out


def find_entry(ort: str) -> dict | None:
    key = _norm(ort)
    if not key:
        return None
    entries = {**CATALOG, **_user_catalog()}
    for entry in entries.values():
        aliases = [_norm(a) for a in entry.get("aliase", [])] + [_norm(entry["stadt"])]
        if key in aliases:
            return entry
    for entry in entries.values():  # Teiltreffer, z. B. "strand von tel aviv"
        if any(len(a) > 3 and re.search(rf"\b{re.escape(a)}\b", key) for a in (_norm(x) for x in entry.get("aliase", []))):
            return entry
    return None


def check_live(video: str) -> dict | None:
    """Prueft eine YouTube-ID: live UND einbettbar? Gibt Titel zurueck oder None. Ergebnis 15 min im Cache."""
    if not re.fullmatch(r"[\w-]{11}", video or ""):
        return None
    now = time.monotonic()
    with _cache_lock:
        hit = _live_cache.get(video)
        if hit and now - hit[0] < CACHE_SECONDS:
            return hit[1]
    result = None
    try:
        r = requests.get(f"https://www.youtube.com/watch?v={video}", headers=UA, timeout=TIMEOUT)
        html = r.text
        if r.ok and '"isLiveNow":true' in html and '"playableInEmbed":true' in html:
            m = re.search(r'<meta name="title" content="([^"]*)"', html)
            result = {"video": video, "titel_youtube": _unescape(m.group(1)) if m else ""}
    except requests.RequestException:
        return None  # Netzproblem: nicht als "nicht live" merken
    with _cache_lock:
        _live_cache[video] = (now, result)
    return result


def _unescape(text: str) -> str:
    import html

    return html.unescape(text)


def _search_youtube(ort: str, limit: int = 6) -> list[str]:
    key = config.get("youtube_api_key")
    query = f"{ort} live cam"
    if key:
        r = requests.get("https://www.googleapis.com/youtube/v3/search", timeout=TIMEOUT, params={
            "part": "id", "type": "video", "eventType": "live", "videoEmbeddable": "true", "maxResults": limit,
            "q": query, "key": key})
        r.raise_for_status()
        return [i["id"]["videoId"] for i in r.json().get("items", []) if i.get("id", {}).get("videoId")]
    if str(config.get("kamera_suche", "youtube_seite")).lower() in {"aus", "false", "0"}:
        return []
    url = "https://www.youtube.com/results?" + urllib.parse.urlencode({"search_query": query, "sp": "EgJAAQ=="})
    html = requests.get(url, headers=UA, timeout=TIMEOUT).text
    return list(dict.fromkeys(re.findall(r'"videoId":"([\w-]{11})"', html)))[:limit]


def _https(value) -> str:
    if not isinstance(value, str):
        return ""
    parts = urllib.parse.urlsplit(value)
    return value if parts.scheme == "https" and parts.hostname and not parts.username and not parts.password else ""


def _windy(ort: str, radius_km: int = 25) -> dict | None:
    """Windy-Webcams in der Naehe (API v3, Format wie in World Monitor): live-Player > Tageszeitraffer > Standbild."""
    key = config.get("windy_api_key")
    if not key:
        return None
    from .extras import _geocode

    lat, lon, label = _geocode(ort)
    r = requests.get("https://api.windy.com/webcams/api/v3/webcams", timeout=TIMEOUT,
                     headers={"x-windy-api-key": key, "User-Agent": UA["User-Agent"]},
                     params={"nearby": f"{lat:.4f},{lon:.4f},{radius_km}", "limit": 20,
                             "include": "player,images,location,urls", "lang": "de"})
    if r.status_code in (401, 403):
        raise ToolFailure("Windy-Schlüssel ungültig oder nicht freigeschaltet (api.windy.com prüfen).")
    r.raise_for_status()
    import math

    want = re.sub(r"[^a-z0-9]", "", _norm(ort))
    candidates = []
    for cam in r.json().get("webcams", []) or []:
        if cam.get("status") == "inactive":
            continue
        player, images = cam.get("player") or {}, (cam.get("images") or {}).get("current") or {}
        live, day = _https(player.get("live")), _https(player.get("day"))
        image = _https(images.get("preview")) or _https(images.get("thumbnail"))
        rank, url, art = (0, live, "windy") if live else (1, day, "windy") if day else (2, image, "bild") if image else (9, "", "")
        if rank == 9:
            continue
        loc = cam.get("location") or {}
        city = str(loc.get("city") or "")
        in_city = bool(want) and (want in re.sub(r"[^a-z0-9]", "", _norm(city)) or
                                  want in re.sub(r"[^a-z0-9]", "", _norm(str(cam.get("title") or ""))))
        try:
            dist = math.dist((lat, lon), (float(loc["latitude"]), float(loc["longitude"]))) * 111
        except (KeyError, TypeError, ValueError):
            dist = 999.0
        # Reihenfolge: in der Stadt selbst > live > Zeitraffer > Standbild > naeher
        candidates.append(((0 if in_city else 1, rank, dist), {
            "art": art, "url": url, "titel": str(cam.get("title") or "Webcam")[:120],
            "stadt": city if city and city.lower() != "unknown" else label.split(",")[0], "live": rank == 0,
            "quelle": "Webcams provided by Windy.com" + ("" if rank == 0 else " (Zeitraffer der letzten 24 h)" if rank == 1 else " (Standbild)"),
            "link": _https((cam.get("urls") or {}).get("detail")) or "https://www.windy.com/webcams",
            "entfernung_km": round(dist, 1), "in_stadt": in_city,
            "hinweis": f"Bild aktualisiert {cam.get('lastUpdatedOn', '')[:16].replace('T', ' ')} UTC" if cam.get("lastUpdatedOn") else ""}))
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0])
    return candidates[0][1]


def _show(cam: dict) -> None:
    global _current
    from .. import runtime

    _current = cam
    runtime.bus.publish({"typ": "kamera", "aktion": "zeigen", "kamera": cam})


def live_kamera(ort: str) -> str:
    ort = str(ort).strip()[:80]
    if not ort:
        raise ToolFailure("Welcher Ort? Zum Beispiel Berlin, Tel Aviv, Paris, New York.")
    force_stream = bool(re.search(r"live[- ]?raster|livestream|youtube", ort, re.I))
    entry = find_entry(re.sub(r"live[- ]?raster|livestream|youtube", " ", ort, flags=re.I))
    cam = None
    windy_error = ""
    if entry and entry.get("hinweis") and not force_stream:
        # Katalog hat fuer diesen Ort nur einen Ersatz (z. B. Tel Aviv): erst echte Kamera vor Ort bei Windy suchen.
        try:
            cam = _windy(entry["stadt"])
        except ToolFailure as exc:
            windy_error = str(exc)
        except (requests.RequestException, KeyError, ValueError):
            cam = None
    if entry and cam is None:
        for src in entry.get("quellen", []):
            live = check_live(src["video"])
            if live:
                cam = {"art": "youtube", "video": src["video"], "titel": src.get("titel") or live["titel_youtube"],
                       "stadt": entry["stadt"], "quelle": "YouTube-Livestream", "live": True,
                       "link": f"https://www.youtube.com/watch?v={src['video']}", "hinweis": entry.get("hinweis", "")}
                break
    if cam is None:
        place = entry["stadt"] if entry else ort
        try:
            for vid in _search_youtube(place):
                live = check_live(vid)
                if live and _norm(place).split()[0] in _norm(live["titel_youtube"]):
                    cam = {"art": "youtube", "video": vid, "titel": live["titel_youtube"][:120], "stadt": place,
                           "quelle": "YouTube-Livestream (gefunden über Suche)", "live": True,
                           "link": f"https://www.youtube.com/watch?v={vid}", "hinweis": ""}
                    break
        except requests.RequestException:
            pass
        if cam is None:
            try:
                cam = _windy(place)
            except ToolFailure as exc:
                windy_error = str(exc)
            except (requests.RequestException, KeyError, ValueError):
                cam = None
    if cam is None:
        if windy_error:
            raise ToolFailure(f"Keine laufende Live-Kamera für {ort} gefunden. {windy_error}")
        raise ToolFailure(f"Für {ort} habe ich gerade keine öffentliche Live-Kamera gefunden, die läuft. Sag ehrlich, "
                          "dass nichts live ist, und biete eine Nachbarstadt oder die Nachrichtenlage an.")
    _show(cam)
    extra = f" Hinweis: {cam['hinweis']}" if cam.get("hinweis") else ""
    if cam.get("art") != "youtube" and not cam.get("live") and entry:
        # Vor Ort nur Zeitraffer/Standbild: echte Live-Alternative aus dem Katalog anbieten (falls eine laeuft)
        alt = next((src for src in entry.get("quellen", []) if check_live(src["video"])), None)
        if alt:
            extra += (f" Alternative live: '{alt.get('titel', '')}' (YouTube) – biete an, darauf umzuschalten; "
                      f"dafür live_kamera mit ort='{entry['stadt']} live-raster' aufrufen.")
    if windy_error:
        extra += f" ({windy_error})"
    kind = "live" if cam.get("live") else ("Standbild" if cam.get("art") == "bild" else "Zeitraffer")
    return (f"Kamera wird jetzt in der Oberfläche gezeigt: {cam['titel']} ({cam['stadt']}, {cam['quelle']}, {kind}).{extra} "
            "Sag in einem Satz, was zu sehen ist (nur Titel/Ort – das Bild selbst kannst du nicht sehen).")


def kamera_schliessen() -> str:
    global _current
    from .. import runtime

    _current = None
    runtime.bus.publish({"typ": "kamera", "aktion": "schliessen"})
    return "Kamera-Ansicht geschlossen."


def tools() -> list[Tool]:
    return [
        Tool("live_kamera", "Schaltet auf eine öffentliche Live-Webcam eines Ortes und zeigt sie groß in der Oberfläche "
                            "(z. B. 'Zeig mir, was gerade in Tel Aviv los ist', 'Berlin live'). Prüft vorher, ob der Stream "
                            "wirklich live ist. Öffentliche Panorama-/Stadtkameras, keine Überwachung von Personen.",
             {"ort": string("Stadt oder Ort, z. B. 'Berlin', 'Tel Aviv', 'New York', 'ISS'")}, live_kamera, ["ort"]),
        Tool("kamera_schliessen", "Schließt die Kamera-Ansicht ('Kamera zu', 'zurück', 'schließ das').",
             {}, kamera_schliessen, []),
    ]
