"""Optionaler Jev-Aufruf für eine klar begrenzte Ja/Nein-Prüfung.

Jev liefert eine strukturierte Wahrscheinlichkeit, keinen Gesprächstext. Dieses Werkzeug ist nur mit Opt-in und
TypeSafe-Schlüssel verfügbar und wird ausschließlich auf ausdrücklichen Wunsch verwendet.
"""

from __future__ import annotations

import math

import requests

from . import config
from .memory import _redact
from .tools import Tool, ToolFailure, string

API = "https://api.typesafe.ai/v1/systemone"


def enabled() -> bool:
    flag = str(config.get("jev", "aus") or "aus").strip().lower()
    return flag in {"an", "true", "1", "yes"} and bool(config.get("typesafe_api_key"))


def _clean(value: str, limit: int) -> str:
    return _redact(str(value or "").strip())[:limit]


def pruefen(inhalt: str, aussage: str, ja_kriterium: str = "", nein_kriterium: str = "") -> str:
    """Prüft die vom Nutzer bezeichnete Aussage anhand des vorgelegten Inhalts."""
    if not enabled():
        raise ToolFailure("Jev ist nicht eingeschaltet. TYPESAFE_API_KEY setzen und JARVIS_JEV=an konfigurieren.")
    text, claim = _clean(inhalt, 12000), _clean(aussage, 1000)
    if not text or not claim:
        raise ToolFailure("Inhalt und Aussage müssen ausgefüllt sein.")

    criteria = {}
    if ja_kriterium.strip():
        criteria["true"] = _clean(ja_kriterium, 500)
    if nein_kriterium.strip():
        criteria["false"] = _clean(nein_kriterium, 500)
    question = {"type": "noul", "instructions": claim}
    if criteria:
        question["criteria"] = criteria
    payload = {"model": config.get("jev_model", "jev-latest"), "state": text,
               "questions": {"entscheidung": question}}
    try:
        response = requests.post(API, headers={"Authorization": f"Bearer {config.get('typesafe_api_key')}"},
                                 json=payload, timeout=(3, 12))
    except requests.RequestException as exc:
        raise ToolFailure(f"Jev ist nicht erreichbar ({type(exc).__name__}).") from None
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise ToolFailure(f"Jev-Anfrage fehlgeschlagen ({response.status_code}): {str(detail)[:240]}")
    try:
        data = response.json()
        answer = data["answers"]["entscheidung"]
        value = float(answer["noul"])
    except (ValueError, TypeError, KeyError, IndexError):
        raise ToolFailure("Jev hat keine gültige strukturierte Ja/Nein-Antwort geliefert.") from None
    if answer.get("type") != "noul" or not math.isfinite(value) or not 0 <= value <= 1:
        raise ToolFailure("Jev hat eine ungültige Ja/Nein-Wahrscheinlichkeit geliefert.")
    model = str(data.get("model") or config.get("jev_model", "jev-latest"))
    return (f"Jev ({model}): Wahrscheinlichkeit für Ja {value:.0%} (Noul {value:.3f}). "
            "Das ist eine Modellentscheidung, kein Beweis und keine Freigabe für eine Aktion.")


def tools() -> list[Tool]:
    if not enabled():
        return []
    return [Tool(
        "systemone_pruefen",
        "Prüft auf ausdrücklichen Wunsch mit Jev, wie wahrscheinlich eine Ja/Nein-Aussage anhand eines vorgelegten Texts ist. "
        "Sendet diesen Text an TypeSafe. Das Ergebnis ist kein Beweis und erlaubt keine Aktion.",
        {"inhalt": string("Text, den Jev prüfen soll"),
         "aussage": string("konkrete Ja/Nein-Aussage oder Frage"),
         "ja_kriterium": string("optional: was als Ja zählen soll"),
         "nein_kriterium": string("optional: was als Nein zählen soll")},
        pruefen, ["inhalt", "aussage"], timeout=15,
    )]
