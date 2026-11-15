"""Sprach-Türsteher: Entscheidet für jede gesprochene Äußerung, ob Jarvis antwortet, nur aufhört oder sie ignoriert.

Aufbau nach TypeSafe-Muster: Jev (TypeSafe System One) liefert einzelne Urteile als Wahrscheinlichkeiten, die
Entscheidung trifft die Wahrheitstabelle in ``entscheide`` – sichtbar und testbar. Alle Fragen, Kriterien und Schwellen
stehen in diesem Modul.

Gesendet wird nur, was die Fragen brauchen: die erkannte Äußerung und (gekürzt) was Jarvis gerade sagt bzw. zuletzt
gesagt hat. Keine CRM-, Kalender- oder Gedächtnisdaten. Abschalten: ``"typesafe_sprachpruefung": "aus"`` in
``~/.jarvis.json``. Ohne Schlüssel (``TYPESAFE_API_KEY``) oder bei einem Fehler gilt das bisherige Verhalten
(antworten), und der Grund steht im Ergebnis.
"""

from __future__ import annotations

import re
import time

from . import config

API_URL = "https://api.typesafe.ai/v1/systemone"
# Fest auf eine Version: die Schwellen gelten für dieses Modell. Umstellen mit ``typesafe_modell``.
MODELL = "jev-1.14.0"
# Gesprochene Sprache wartet: lieber ohne Urteil weiter als den Nutzer hängen lassen.
ZEITLIMIT_S = 2.5
JA, NEIN = 0.7, 0.3
KONTEXT_ZEICHEN = 240  # nur der letzte Satz von Jarvis, nicht seine ganze Antwort
AEUSSERUNG_ZEICHEN = 600

ANTWORTEN, STOPPEN, IGNORIEREN = "antworten", "stoppen", "ignorieren"

# Name nur am Anfang („Jarvis, …“, „Hey Jarvis …“): ein „Jarvis“ mitten im Fernsehton zählt nicht als Ansprache.
WAKE = re.compile(r"^\W*(?:(?:hey|hallo|ok(?:ay)?|ya|يا)\W+)?(jarvis|dscharvis|jarwis|javis|jervis|جارفيس)\b", re.I)
STOP = re.compile(r"^\W*(?:jarvis\W+)?(stopp?|stop|halt|ruhe|sei still|genug|enough|quiet|be quiet|hold on|that's enough|aufh[oö]ren|hör auf|hoer auf|اسكت|كفاية|خلاص|بس|استنى)\W*$", re.I)

# --- Fragen an Jev (eine Anfrage, gleiche Zustandsdaten) --------------------------------------------------------
# Englisch formuliert, die Äußerung bleibt im Original (Deutsch, Englisch oder Ägyptisch).
FRAGEN: dict[str, dict] = {
    # Live gemessen (15.11.2026): echte Sätze ≥ 0,94, Kauderwelsch aus Störgeräuschen 0,29–0,48.
    "verstaendlich": {
        "type": "noul",
        "instructions": "Do the words in `heard` fit together into a meaningful sentence or phrase?",
        "criteria": {
            "true": "A meaningful sentence, question, command or short reply such as 'Yes.' or 'And tomorrow?'",
            "false": "Words that do not fit together into any meaning, as when speech recognition turns background "
                     "noise into random words",
        },
    },
    # Fernseher und Radio: echte Befehle ≤ 0,12, Ansagen und Werbung um 0,55.
    "sendung": {
        "type": "noul",
        "instructions": "Does `heard` sound like it comes from a broadcast, such as news, a weather report, an advert "
                        "or dialogue in a film?",
        "criteria": {
            "true": "Phrasing typical of a presenter, reporter, advert or scripted scene",
            "false": "Phrasing of a person speaking to an assistant or to someone in the room",
        },
    },
    "an_jarvis": {
        "type": "noul",
        "instructions": "Is `heard` addressed to the voice assistant Jarvis?",
        "criteria": {
            "true": "A question, request, correction or reply directed at Jarvis, also without saying its name, "
                    "for example an answer to what `jarvis_said`",
            "false": "Talk with another person in the room, a phone call, sound from a TV, radio or video, or the "
                     "speaker talking to themselves",
        },
    },
    "stopp_wunsch": {
        "type": "noul",
        "instructions": "Does the speaker of `heard` want Jarvis to stop talking?",
        "criteria": {
            "true": "Asks Jarvis to stop, be quiet, wait, or says that what Jarvis said is enough",
            "false": "Asks or tells Jarvis something without asking it to stop talking",
        },
    },
    "mehr_als_stopp": {
        "type": "noul",
        "instructions": "Does `heard` contain a question or request for Jarvis other than to stop talking?",
        "criteria": {
            "true": "Contains a new question, a new task, or a correction of the current task",
            "false": "Only asks Jarvis to stop, possibly with a thank-you",
        },
    },
    "nur_rueckmeldung": {
        "type": "noul",
        "instructions": "Is `heard` only a short acknowledgement while listening, such as 'mhm', 'okay' or "
                        "'yes, exactly'?",
        "criteria": {
            "true": "A listening sound or a one- or two-word agreement with nothing else",
            "false": "Contains a question, a request, new information, or disagreement",
        },
    },
}


def _modell() -> str:
    return str(config.get("typesafe_modell", MODELL) or MODELL)


def _schluessel() -> str:
    return str(config.get("typesafe_api_key") or "")


def eingeschaltet() -> bool:
    """Einstellung (Standard an); wirkt nur mit Schlüssel."""
    return str(config.get("typesafe_sprachpruefung", "an") or "an").lower() not in ("aus", "false", "0", "nein")


def aktiv() -> bool:
    """Prüfung eingeschaltet und Schlüssel vorhanden."""
    return eingeschaltet() and bool(_schluessel())


def status() -> dict:
    """Für Einstellungen und Diagnose: ohne den Schlüssel selbst."""
    problem = None if _schluessel() else "TYPESAFE_API_KEY fehlt"
    return {"an": eingeschaltet(), "aktiv": aktiv(), "modell": _modell(), "problem": problem}


def einschalten(an: bool) -> None:
    config.save("typesafe_sprachpruefung", None if an else "aus")


def letzter_satz(text: str) -> str:
    """Nur der letzte (ggf. angefangene) Satz von Jarvis – vorgelesene Termine oder Kontakte davor bleiben lokal."""
    text = " ".join((text or "").split())
    teile = [t for t in re.split(r"(?<=[.!?؟])\s+", text) if t.strip()]
    return (teile[-1] if teile else "")[-KONTEXT_ZEICHEN:]


def zustand(gehoert: str, kontext: str, jarvis_spricht: bool) -> dict:
    """Zustandsdaten für Jev: nur, was die Fragen brauchen; die Situation stellt der Code als Tatsache fest."""
    return {
        "situation": ("Jarvis is answering right now; `heard` was picked up by the microphone during its answer."
                      if jarvis_spricht else "Jarvis has finished speaking and is waiting for input."),
        "jarvis_said": letzter_satz(kontext),
        "heard": (gehoert or "")[:AEUSSERUNG_ZEICHEN],
    }


def anfrage(gehoert: str, kontext: str, jarvis_spricht: bool) -> dict:
    """Die genaue Anfrage (zum Protokollieren und Wiederholen)."""
    return {"model": _modell(), "state": zustand(gehoert, kontext, jarvis_spricht), "questions": FRAGEN}


_client = None


def _verbindung():
    """Eine offene Verbindung für alle Prüfungen: spart den TLS-Aufbau (gemessen rund 100 ms je Äußerung)."""
    global _client
    import httpx

    if _client is None:
        _client = httpx.Client(timeout=httpx.Timeout(ZEITLIMIT_S, connect=1.5),
                               limits=httpx.Limits(max_keepalive_connections=2, keepalive_expiry=120))
    return _client


def frage_jev(gehoert: str, kontext: str, jarvis_spricht: bool, client=None) -> dict[str, float]:
    """Eine Anfrage an TypeSafe; liefert {frage: Wahrscheinlichkeit}. Fehler werden geworfen, nie als „nein“ gedeutet."""
    import httpx

    body = anfrage(gehoert, kontext, jarvis_spricht)
    headers = {"Authorization": f"Bearer {_schluessel()}", "Content-Type": "application/json"}
    response = (client or _verbindung()).post(API_URL, json=body, headers=headers)
    if response.status_code >= 400:
        raise RuntimeError(f"TypeSafe HTTP {response.status_code}")
    answers = response.json().get("answers") or {}
    werte = {}
    for name in FRAGEN:
        value = (answers.get(name) or {}).get("noul")
        if not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise RuntimeError(f"TypeSafe-Antwort ohne gültigen Wert für {name}")
        werte[name] = float(value)
    return werte


def _stufe(p: float | None) -> str:
    if p is None:
        return "unklar"
    return "ja" if p > JA else "nein" if p < NEIN else "unklar"


def entscheide(werte: dict[str, float], jarvis_spricht: bool) -> tuple[str, str]:
    """Wahrheitstabelle: Urteile von Jev -> Aktion. Jede Kombination hat ein Ergebnis."""
    v = _stufe(werte.get("verstaendlich"))
    b = _stufe(werte.get("sendung"))
    a = _stufe(werte.get("an_jarvis"))
    s = _stufe(werte.get("stopp_wunsch"))
    m = _stufe(werte.get("mehr_als_stopp"))
    r = _stufe(werte.get("nur_rueckmeldung"))
    if v == "nein":
        return IGNORIEREN, "unverständlich"
    if jarvis_spricht and s == "ja" and m != "ja":
        return STOPPEN, "stopp"
    if v == "unklar":
        # Echte Sätze sind fast immer eindeutig verständlich; im Unklar-Band liegen Störgeräusche und verhörte Fetzen.
        # Lieber nicht antworten und anzeigen, dass es nochmal gesagt werden soll.
        return IGNORIEREN, "nicht sicher verstanden"
    if a == "nein":
        return IGNORIEREN, "nicht an Jarvis"
    if b != "nein":
        return IGNORIEREN, "klang nach Sendung"
    if jarvis_spricht and r == "ja":
        return IGNORIEREN, "nur Rückmeldung"
    if jarvis_spricht and a == "unklar":
        # Während er spricht: im Zweifel weiterreden (Esc und „Jarvis, stopp“ wirken immer).
        return IGNORIEREN, "unklar, ob an Jarvis"
    return ANTWORTEN, "an Jarvis"


def regeln(gehoert: str, jarvis_spricht: bool) -> tuple[str, str] | None:
    """Eindeutige Fälle ohne Modell."""
    t = (gehoert or "").strip()
    if not t:
        return IGNORIEREN, "leer"
    if STOP.match(t):
        return (STOPPEN, "stopp-wort") if jarvis_spricht else (IGNORIEREN, "stopp ohne laufende Antwort")
    if WAKE.search(t):
        return ANTWORTEN, "name genannt"
    return None


def pruefe(gehoert: str, kontext: str = "", jarvis_spricht: bool = False, client=None) -> dict:
    """Ergebnis: {aktion, grund, quelle, werte, ms}. Ohne Prüfung oder bei Fehlern: antworten (bisheriges Verhalten)."""
    start = time.monotonic()
    if not aktiv():  # ausgeschaltet oder ohne Schlüssel: auch keine Regeln, alles wie vor dem Türsteher
        return {"aktion": ANTWORTEN, "grund": "prüfung aus", "quelle": "aus", "werte": {}, "ms": 0}
    regel = regeln(gehoert, jarvis_spricht)
    if regel:
        return {"aktion": regel[0], "grund": regel[1], "quelle": "regel", "werte": {}, "ms": 0}
    try:
        werte = frage_jev(gehoert, kontext, jarvis_spricht, client)
    except Exception as exc:
        ms = round((time.monotonic() - start) * 1000)
        _protokoll("fehler", ms, fehlerart=type(exc).__name__)
        return {"aktion": ANTWORTEN, "grund": f"typesafe-fehler: {type(exc).__name__}", "quelle": "fehler",
                "werte": {}, "ms": ms}
    aktion, grund = entscheide(werte, jarvis_spricht)
    ms = round((time.monotonic() - start) * 1000)
    _protokoll(aktion, ms, grund=grund, spricht=jarvis_spricht, **{k: round(v, 3) for k, v in werte.items()})
    return {"aktion": aktion, "grund": grund, "quelle": "typesafe", "werte": werte, "ms": ms}


def _protokoll(status: str, ms: int, **felder) -> None:
    """Ereignis ohne Inhalte (keine Transkripte) in ~/.jarvis/events.jsonl."""
    try:
        from . import events

        events.log("sprachpruefung", status=status, ms=ms, modell=_modell(), **felder)
    except Exception:
        pass
