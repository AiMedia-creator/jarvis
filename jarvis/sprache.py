"""Gesprächssprache: Deutsch, ägyptisches Arabisch (Masri) oder automatisch nach der Sprache des Nutzers.

Einstellung ``sprache`` (JARVIS_SPRACHE oder ~/.jarvis.json): ``de`` (Standard), ``arz`` oder ``auto``.
Betrifft vier Stellen, alle aus dieser Datei gesteuert:

- Erkennung (Whisper): ``de`` -> Deutsch, ``arz`` -> Arabisch, ``auto`` -> Whisper erkennt selbst. Für Arabisch nimmt
  Jarvis das genauere Modell ``small`` (Einstellung ``whisper_model_arabisch``) und einen ägyptischen Vorgabetext,
  damit die Schreibweise beim Dialekt bleibt.
- Antwort (Systemprompt): Arabisch immer im ägyptischen Dialekt, nicht Hocharabisch.
- Stimme: Arabischer Text geht an eine ägyptische Stimme (``stimme_arabisch``, Standard Microsoft „Shakir“, ar-EG).
- Echtzeit (ElevenLabs): Agentensprache ``ar`` bei ``arz``.
"""

from __future__ import annotations

import re

from . import config

SPRACHEN = {"de": "Deutsch", "arz": "Ägyptisch (Masri)", "auto": "Automatisch (Deutsch oder Ägyptisch)"}
STIMME_ARABISCH = "edge:ar-EG-ShakirNeural"
# Standardmodell für Deutsch. "base" ist ~0,45 s schneller je Satz, verhört aber Namen und kurze Antworten
# (Firmennamen wie "Musterhaus Weber" -> "Muster House Weber", "Stopp." -> nichts). Umstellen: whisper_model bzw. JARVIS_WHISPER_MODEL.
WHISPER_STANDARD = "small"
WHISPER_ARABISCH = "small"  # liegt bereits im Cache; „large-v3“ ist genauer, aber auf der CPU deutlich langsamer
# Typische Kairoer Alltagswörter: lenken Whisper auf die ägyptische Schreibweise statt auf Hocharabisch.
VORGABE_MASRI = "إزيك يا ماستر، عايز إيه دلوقتي؟ ماشي، تمام كده، مش عارف، فين المواعيد بتاعة النهارده؟ يلا بينا."
_ARABISCH = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")
_BUCHSTABE = re.compile(r"[^\W\d_]", re.UNICODE)


def aktuell() -> str:
    wert = str(config.get("sprache", "de") or "de").strip().lower()
    return wert if wert in SPRACHEN else "de"


def ist_arabisch(text: str) -> bool:
    """Überwiegend arabische Schrift? (Namen oder Fachbegriffe in lateinischer Schrift ändern daran nichts.)"""
    buchstaben = _BUCHSTABE.findall(str(text or ""))
    if not buchstaben:
        return False
    return sum(1 for b in buchstaben if _ARABISCH.match(b)) / len(buchstaben) >= 0.4


def whisper_sprache() -> str | None:
    """Sprachcode für Whisper: None = selbst erkennen."""
    return {"de": "de", "arz": "ar", "auto": None}[aktuell()]


def whisper_standard() -> str:
    return str(config.get("whisper_model", WHISPER_STANDARD) or WHISPER_STANDARD)


def whisper_modell(standard: str) -> str:
    """Für Arabisch reicht „base“ nicht – dann das genauere Modell (sofern nicht ausdrücklich anders eingestellt)."""
    if aktuell() == "de":
        return standard
    return str(config.get("whisper_model_arabisch", WHISPER_ARABISCH) or WHISPER_ARABISCH)


def waehle(wahrscheinlichkeiten: list[tuple[str, float]]) -> str:
    """„Automatisch“: nur zwischen Deutsch und Arabisch wählen. Freie Erkennung rät bei kurzen Sätzen sonst gern
    Niederländisch oder Englisch und schreibt den Satz dann in der falschen Sprache mit."""
    werte = {code: p for code, p in wahrscheinlichkeiten if code in ("de", "ar")}
    return "ar" if werte.get("ar", 0.0) > werte.get("de", 0.0) else "de"


def whisper_vorgabe(deutsch: str) -> str:
    """Vorgabetext für Whisper: deutsches Vokabular bzw. ägyptische Alltagssprache."""
    return {"de": deutsch, "arz": VORGABE_MASRI, "auto": deutsch}[aktuell()]


def stimme_fuer(text: str, stimme: str) -> str:
    """Arabischer Satz -> ägyptische Stimme. Eine deutsche Microsoft-Stimme würde Arabisch nicht sprechen."""
    if not ist_arabisch(text):
        return stimme
    return str(config.get("stimme_arabisch", STIMME_ARABISCH) or STIMME_ARABISCH)


def sprachcode(text: str) -> str:
    """language_code für ElevenLabs."""
    return "ar" if ist_arabisch(text) else "de"


def fuer_sprache(text: str) -> str:
    """Sprechbare Fassung für arabischen Text: nur Markdown und Links entfernen. Die deutsche Normalisierung
    (Uhrzeiten, Beträge, Abkürzungen auf Deutsch) wäre hier falsch."""
    t = re.sub(r"https?://\S+", "الرابط", str(text or ""))
    t = re.sub(r"[*_`#]+", "", t)
    t = re.sub(r"^\s*[-•]\s+", "", t, flags=re.M)
    return re.sub(r"[ \t]{2,}", " ", t).strip()


def anrede_arabisch() -> str:
    name = config.anrede()
    return {"Master": "ماستر"}.get(name, name)


def prompt_satz() -> str:
    """Zusatz für die Systemprompts (Text, Studio-Stimme und Echtzeit)."""
    sprache = aktuell()
    masri = (f"Sprich ägyptisches Arabisch (Masri, Kairoer Alltagssprache) in arabischer Schrift – nicht Hocharabisch "
             f"(Fusha): also „عايز“ statt „أريد“, „دلوقتي“ statt „الآن“, „مش“ statt „ليس“, „إزاي“, „كده“, „فين“, „بتاع“. "
             f"Sprich den Nutzer mit „يا {anrede_arabisch()}“ an. Namen, Firmen, Fachbegriffe, E-Mail-Adressen und Zahlen "
             "bleiben, wie sie sind. Inhalte für Dritte (Mails, CRM-Notizen, Termine) schreibst du in der Sprache, die der "
             "Nutzer dafür verlangt, sonst auf Deutsch.")
    if sprache == "arz":
        return "\n\n# Sprache\nAntworte immer auf Ägyptisch, auch wenn eine Nachricht auf Deutsch kommt. " + masri
    if sprache == "auto":
        return ("\n\n# Sprache\nAntworte in der Sprache der letzten Nutzernachricht: auf Deutsch auf Deutsch, auf Arabisch "
                "immer auf Ägyptisch. " + masri)
    return ""


def begruessung(deutsch: str) -> str:
    """Erster Satz im Echtzeit-Modus."""
    if aktuell() == "arz":
        return f"أهلاً يا {anrede_arabisch()}، كل الأنظمة شغالة. أقدر أساعدك في إيه؟"
    return deutsch


def echtzeit_sprache() -> str:
    """Agentensprache bei ElevenLabs. Bei „auto“ bleibt Deutsch die Grundsprache; der Prompt erlaubt den Wechsel."""
    return "ar" if aktuell() == "arz" else "de"
