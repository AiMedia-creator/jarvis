"""Zusatzwerkzeuge aus OpenJarvis (Apache-2.0), angepasst: Rechner, Wetter, Hacker News, GitHub, PDF.

Alle nur lesend. Wetter nutzt Open-Meteo (kein Schluessel noetig) statt OpenWeatherMap.
"""

from __future__ import annotations

import ast
import math
import operator
from pathlib import Path

import requests

from .. import config
from . import Tool, ToolFailure, cached, integer, string

TIMEOUT = 12
UA = {"User-Agent": "Jarvis/1.0 (lokaler Assistent)"}

# ---------------------------------------------------------------- Rechner (sicher, ohne eval)

_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}
_UN = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS = {"sqrt": math.sqrt, "log": math.log, "log10": math.log10, "ln": math.log, "exp": math.exp,
          "sin": math.sin, "cos": math.cos, "tan": math.tan, "abs": abs, "round": round, "min": min, "max": max,
          "floor": math.floor, "ceil": math.ceil, "wurzel": math.sqrt}
_CONSTS = {"pi": math.pi, "e": math.e}


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and (abs(right) > 1000 or abs(left) > 1e12):
            raise ToolFailure("Potenz zu gross")
        return _BIN[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UN:
        return _UN[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Name) and node.id in _CONSTS:
        return _CONSTS[node.id]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS and not node.keywords:
        return _FUNCS[node.func.id](*[_eval(a) for a in node.args])
    raise ToolFailure("Nur Zahlen, + - * / // % **, Klammern und Funktionen wie sqrt, log, round, min, max erlaubt.")


def rechnen(ausdruck: str) -> str:
    expr = str(ausdruck).strip().replace("^", "**").replace("×", "*").replace("÷", "/")
    # deutsches Dezimalkomma: "1.234,5" -> 1234.5 ; "3,5" -> 3.5 (Kommas als Argumenttrenner nur in Funktionen)
    if "(" not in expr:
        expr = expr.replace(".", "").replace(",", ".") if "," in expr else expr
    if len(expr) > 300:
        raise ToolFailure("Ausdruck zu lang")
    try:
        value = _eval(ast.parse(expr, mode="eval"))
    except ToolFailure:
        raise
    except ZeroDivisionError:
        raise ToolFailure("Division durch null") from None
    except (SyntaxError, ValueError, TypeError, OverflowError) as exc:
        raise ToolFailure(f"Nicht berechenbar: {exc}") from None
    if isinstance(value, float):
        value = round(value, 10)
        if value.is_integer() and abs(value) < 1e15:
            value = int(value)
    return f"{ausdruck} = {value}"


# ---------------------------------------------------------------- Wetter (Open-Meteo)

_WMO = {0: "klar", 1: "überwiegend klar", 2: "teils bewölkt", 3: "bedeckt", 45: "Nebel", 48: "Reifnebel",
        51: "leichter Nieselregen", 53: "Nieselregen", 55: "starker Nieselregen", 61: "leichter Regen", 63: "Regen",
        65: "starker Regen", 66: "gefrierender Regen", 67: "starker gefrierender Regen", 71: "leichter Schneefall",
        73: "Schneefall", 75: "starker Schneefall", 77: "Schneegriesel", 80: "leichte Regenschauer",
        81: "Regenschauer", 82: "heftige Regenschauer", 85: "Schneeschauer", 86: "starke Schneeschauer",
        95: "Gewitter", 96: "Gewitter mit Hagel", 99: "schweres Gewitter mit Hagel"}


def _geocode(ort: str) -> tuple[float, float, str]:
    r = requests.get("https://geocoding-api.open-meteo.com/v1/search", headers=UA, timeout=TIMEOUT,
                     params={"name": ort, "count": 1, "language": "de", "format": "json"})
    r.raise_for_status()
    hits = r.json().get("results") or []
    if not hits:
        raise ToolFailure(f"Ort '{ort}' nicht gefunden")
    h = hits[0]
    label = ", ".join(x for x in (h.get("name"), h.get("admin1"), h.get("country")) if x)
    return float(h["latitude"]), float(h["longitude"]), label


@cached(600)
def wetter(ort: str = "", tage: int = 1) -> str:
    ort = (ort or config.get("wetter_ort") or "").strip()
    if not ort:
        raise ToolFailure("Kein Ort angegeben und kein Standardort gesetzt (Einstellung 'wetter_ort' in ~/.jarvis.json).")
    tage = max(1, min(7, int(tage or 1)))
    lat, lon, label = _geocode(ort[:120])
    r = requests.get("https://api.open-meteo.com/v1/forecast", headers=UA, timeout=TIMEOUT, params={
        "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": tage,
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum",
    })
    r.raise_for_status()
    data = r.json()
    cur = data.get("current") or {}
    lines = [f"Wetter {label} (Quelle: Open-Meteo, Stand {cur.get('time', '?')} Ortszeit)",
             f"Jetzt: {_WMO.get(cur.get('weather_code'), 'unbekannt')}, {cur.get('temperature_2m')} °C "
             f"(gefühlt {cur.get('apparent_temperature')} °C), Wind {cur.get('wind_speed_10m')} km/h, "
             f"Luftfeuchte {cur.get('relative_humidity_2m')} %"]
    d = data.get("daily") or {}
    for i, day in enumerate(d.get("time") or []):
        lines.append(f"{day}: {_WMO.get((d.get('weather_code') or [None])[i], 'unbekannt')}, "
                     f"{d['temperature_2m_min'][i]} bis {d['temperature_2m_max'][i]} °C, "
                     f"Regenrisiko {d['precipitation_probability_max'][i]} %, Niederschlag {d['precipitation_sum'][i]} mm")
    return "\n".join(lines)


# ---------------------------------------------------------------- Hacker News

_HN = "https://hacker-news.firebaseio.com/v0"


@cached(300)
def hacker_news(anzahl: int = 8) -> str:
    anzahl = max(1, min(20, int(anzahl or 8)))
    r = requests.get(f"{_HN}/topstories.json", headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    lines = ["Hacker News – Top-Storys (Quelle: news.ycombinator.com):"]
    for sid in (r.json() or [])[:anzahl]:
        try:
            item = requests.get(f"{_HN}/item/{int(sid)}.json", headers=UA, timeout=TIMEOUT).json() or {}
        except (requests.RequestException, ValueError):
            continue
        url = item.get("url") or f"https://news.ycombinator.com/item?id={sid}"
        lines.append(f"- {item.get('title', '?')} ({item.get('score', 0)} Punkte, {item.get('descendants', 0)} "
                     f"Kommentare) {url}")
    return "\n".join(lines)


# ---------------------------------------------------------------- GitHub-Benachrichtigungen

def github_configured() -> bool:
    return bool(config.get("github_token"))


@cached(60)
def github_benachrichtigungen(anzahl: int = 15) -> str:
    token = config.get("github_token")
    if not token:
        raise ToolFailure("GitHub ist nicht eingerichtet: export JARVIS_GITHUB_TOKEN='…' (Fine-grained Token, "
                          "nur Leserecht 'Notifications').")
    r = requests.get("https://api.github.com/notifications", timeout=TIMEOUT,
                     params={"per_page": max(1, min(50, int(anzahl or 15)))},
                     headers={**UA, "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
    if r.status_code == 401:
        raise ToolFailure("GitHub-Token ungültig oder abgelaufen")
    r.raise_for_status()
    items = r.json() or []
    if not items:
        return "Keine ungelesenen GitHub-Benachrichtigungen."
    lines = [f"{len(items)} ungelesene GitHub-Benachrichtigungen:"]
    for n in items:
        subj = n.get("subject") or {}
        lines.append(f"- [{(n.get('repository') or {}).get('full_name', '?')}] {subj.get('type', '')}: "
                     f"{subj.get('title', '?')} (Grund: {n.get('reason', '?')}, {n.get('updated_at', '')})")
    return "\n".join(lines)


# ---------------------------------------------------------------- PDF lesen (lokal)

MAX_PDF_CHARS = 30000


def pdf_lesen(pfad: str, seiten: str = "") -> str:
    path = Path(str(pfad)).expanduser().resolve()
    home = Path.home().resolve()
    if home not in path.parents:
        raise ToolFailure("Nur PDFs innerhalb deines Benutzerordners")
    if path.suffix.lower() != ".pdf" or not path.is_file():
        raise ToolFailure(f"Keine PDF-Datei gefunden: {path}")
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ToolFailure("PDF-Unterstützung fehlt: pip install pypdf") from None
    reader = PdfReader(str(path))
    total = len(reader.pages)
    wanted = range(total)
    if seiten:
        try:
            a, _, b = str(seiten).partition("-")
            start, end = int(a), int(b or a)
            wanted = range(max(1, start) - 1, min(total, end))
        except ValueError:
            raise ToolFailure("Seiten als '3' oder '2-5' angeben") from None
    parts = []
    for i in wanted:
        parts.append(f"--- Seite {i + 1} ---\n{(reader.pages[i].extract_text() or '').strip()}")
        if sum(map(len, parts)) > MAX_PDF_CHARS:
            parts.append("[... gekürzt; mit 'seiten' gezielt weiterlesen]")
            break
    return f"{path.name} ({total} Seiten)\n" + "\n".join(parts)


def tools() -> list[Tool]:
    return [
        Tool("rechnen", "Rechnet exakt (statt im Kopf): Grundrechenarten, Prozent, Potenzen, sqrt, log, round, min, max. "
                        "Nutze es für jede Zahl, die stimmen muss (Margen, Preise, Hochrechnungen).",
             {"ausdruck": string("z. B. '12500*0.19' oder '(4200-3100)/3100*100'")}, rechnen, ["ausdruck"]),
        Tool("wetter", "Aktuelles Wetter und Vorhersage bis 7 Tage für einen Ort (Open-Meteo).",
             {"ort": string("Ort, z. B. 'Berlin'; leer = Standardort"), "tage": integer("1-7, Standard 1")},
             wetter, []),
        Tool("hacker_news", "Aktuelle Top-Storys von Hacker News (Tech, Startups, KI) mit Punkten und Link.",
             {"anzahl": integer("1-20, Standard 8")}, hacker_news, []),
        Tool("github_benachrichtigungen", "Ungelesene GitHub-Benachrichtigungen (Issues, PRs, Reviews).",
             {"anzahl": integer("1-50, Standard 15")}, github_benachrichtigungen, []),
        Tool("pdf_lesen", "Liest den Text einer lokalen PDF-Datei im Benutzerordner (z. B. ~/Downloads/vertrag.pdf).",
             {"pfad": string("Dateipfad, ~ erlaubt"), "seiten": string("optional: '3' oder '2-5'")}, pdf_lesen, ["pfad"]),
    ]
