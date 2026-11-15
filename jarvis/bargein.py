"""Intelligentes Unterbrechen: Nur wenn der Nutzer Jarvis wirklich meint – nicht bei Stimmen im Raum.

Erst schnelle Regeln (Stopp-Woerter, "Jarvis", Fuellwoerter), dann der Sprach-Tuersteher (TypeSafe, sprachpruefung.py)
oder ohne ihn eine blitzschnelle Einschaetzung durch das kleinste Modell. Im Zweifel wird NICHT unterbrochen (Stopp/Esc funktionieren immer).
"""

from __future__ import annotations

import json
import re

from . import config

STOP = re.compile(r"^\W*(stopp?|halt|warte|moment|ruhe|still|sei still|genug|danke,? das reicht|aufh[oö]ren)\b", re.I)
WAKE = re.compile(r"\b(jarvis|dscharvis|jarwis|javis|jervis)\b", re.I)
FILLER = re.compile(r"^\W*(mhm+|hm+|aha|ok(ay)?|ja|ja genau|genau|stimmt|richtig|alles klar|super|danke|ah|oh|äh+|ähm+)\W*$", re.I)

PROMPT = """Du entscheidest blitzschnell, ob ein im Raum gehoerter Satz an den Sprachassistenten Jarvis gerichtet ist
und rechtfertigt, dass Jarvis seine laufende Antwort abbricht.
UNTERBRECHEN: Der Nutzer korrigiert Jarvis, ergaenzt seinen Auftrag, stellt Jarvis eine neue Frage, gibt einen neuen
Auftrag oder will, dass Jarvis aufhoert.
NICHT UNTERBRECHEN: Gespraeche mit anderen Personen, Telefonate, Fernseher/Radio/Video, Selbstgespraeche,
zustimmende Laute ("mhm", "ja genau"), Saetze ohne Bezug zu dem, was Jarvis gerade tut.
Antworte NUR mit JSON: {"unterbrechen": true} oder {"unterbrechen": false}"""


def quick_rules(text: str) -> bool | None:
    t = text.strip()
    if not t:
        return False
    if STOP.search(t) or WAKE.search(t):
        return True
    if FILLER.match(t) or len(re.findall(r"\w+", t)) < 3:
        return False
    return None


def should_interrupt(text: str, context: str = "") -> dict:
    rule = quick_rules(text)
    if rule is not None:
        return {"unterbrechen": rule, "grund": "regel"}
    from . import sprachpruefung

    if sprachpruefung.aktiv():  # Sprach-Türsteher (TypeSafe) statt Modell-Einschätzung; bei Fehlern weiter unten
        urteil = sprachpruefung.pruefe(text, context, jarvis_spricht=True)
        if urteil["quelle"] == "typesafe":
            return {"unterbrechen": urteil["aktion"] != sprachpruefung.IGNORIEREN, "grund": f"typesafe:{urteil['grund']}"}
    try:
        from .agent import MODELS, make_client

        client = make_client(timeout=4.0)
        msg = client.messages.create(
            model=config.get("bargein_model") or MODELS["haiku"], max_tokens=12, system=PROMPT,
            messages=[{"role": "user", "content": f"Jarvis sagt gerade: «{context[-400:]}»\nGehoert: «{text[:400]}»"}])
        raw = "".join(getattr(b, "text", "") for b in msg.content)
        m = re.search(r"\{.*\}", raw, re.S)
        verdict = bool(json.loads(m.group(0)).get("unterbrechen")) if m else False
        return {"unterbrechen": verdict, "grund": "modell"}
    except Exception as exc:  # im Zweifel weiterreden
        return {"unterbrechen": False, "grund": f"fehler:{type(exc).__name__}"}
