"""Gesprächssprache: Deutsch, Englisch, ägyptisches Arabisch (Masri) oder automatische Spracherkennung.

Einstellung ``sprache`` (JARVIS_SPRACHE oder ~/.jarvis.json): ``de`` (Standard), ``en``, ``arz`` oder ``auto``.
Betrifft vier Stellen, alle aus dieser Datei gesteuert:

- Erkennung (Whisper): Deutsch, Englisch oder Arabisch; in ``auto`` werden diese drei Sprachen erkannt. Für Arabisch
  nimmt Jarvis das genauere Modell ``whisper_model_arabisch`` und einen ägyptischen Vorgabetext.
- Antwort (Systemprompt): Englisch bleibt Englisch; Arabisch bleibt ägyptisches Arabisch, nicht Hocharabisch.
- Stimme: Arabische Antworten nutzen eine ägyptische Stimme. Englische Antworten wechseln bei einer deutschen
  Microsoft-Stimme auf eine englische Stimme.
- Echtzeit (ElevenLabs): ``auto`` konfiguriert Sprachprofile und den Sprachwechsel für Deutsch, Englisch und Arabisch.
"""

from __future__ import annotations

import re

from . import config

SPRACHEN = {"de": "Deutsch", "en": "Englisch", "arz": "Ägyptisch (Masri)",
            "auto": "Automatisch (Deutsch, Englisch oder Ägyptisch)"}
STIMME_ARABISCH = "edge:ar-EG-ShakirNeural"
STIMME_ENGLISCH = "edge:en-GB-RyanNeural"
# Standardmodell für Deutsch. "base" ist ~0,45 s schneller je Satz, verhört aber Namen und kurze Antworten
# (Firmennamen wie "Musterhaus Weber" -> "Muster House Weber", "Stopp." -> nichts). Umstellen: whisper_model bzw. JARVIS_WHISPER_MODEL.
WHISPER_STANDARD = "small"
WHISPER_ARABISCH = "small"  # liegt bereits im Cache; „large-v3“ ist genauer, aber auf der CPU deutlich langsamer
# Typische Kairoer Alltagswörter: lenken Whisper auf die ägyptische Schreibweise statt auf Hocharabisch.
VORGABE_MASRI = "إزيك يا ماستر، عايز إيه دلوقتي؟ ماشي، تمام كده، مش عارف، فين المواعيد بتاعة النهارده؟ يلا بينا."
VORGABE_ENGLISCH = "Jarvis, Master, HubSpot, Gmail, Obsidian, follow-up, calendar, project."
_ARABISCH = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")
_BUCHSTABE = re.compile(r"[^\W\d_]", re.UNICODE)
_ENGLISH_CUES = frozenset("a absolutely an and are as at be been but by can could did do does exactly for from get good got great have he hello her here hi how i if in into is it its just let me morning my no of okay on or our please right she so sounds sure thank thanks that the their them there they this to was we were what when where who will with would yes you your".split())
_GERMAN_CUES = frozenset("aber alle als am an auch auf aus bei bin bis bist das dass dem den der des die dir doch du durch ein eine einer einem einen es für ganz gegen gewesen hat hatte hier ich im in ist ja kein keine mit nach nicht noch nur oder sein seine sich sie sind so über um und uns unser von vor war waren was wer wie wir wird wo zu zum zur".split())


def aktuell() -> str:
    wert = str(config.get("sprache", "de") or "de").strip().lower()
    return wert if wert in SPRACHEN else "de"


def ist_arabisch(text: str) -> bool:
    """Überwiegend arabische Schrift? (Namen oder Fachbegriffe in lateinischer Schrift ändern daran nichts.)"""
    buchstaben = _BUCHSTABE.findall(str(text or ""))
    if not buchstaben:
        return False
    return sum(1 for b in buchstaben if _ARABISCH.match(b)) / len(buchstaben) >= 0.4


def text_sprache(text: str) -> str:
    """Grobe Sprachwahl für Sprachausgabe. Arabische Schrift ist eindeutig; de/en werden nur in Auto heuristisch getrennt."""
    text = str(text or "")
    if ist_arabisch(text):
        return "ar"
    mode = aktuell()
    if mode == "en":
        return "en"
    if mode == "arz":
        return "ar"
    if mode == "de":
        return "de"
    if re.search(r"[äöüßÄÖÜ]", text):
        return "de"
    words = re.findall(r"[a-zA-Z']+", text.lower())
    english = sum(word in _ENGLISH_CUES for word in words)
    german = sum(word in _GERMAN_CUES for word in words)
    return "en" if english > german else "de"


def whisper_sprache() -> str | None:
    """Sprachcode für Whisper: None = selbst erkennen."""
    return {"de": "de", "en": "en", "arz": "ar", "auto": None}[aktuell()]


def whisper_standard() -> str:
    return str(config.get("whisper_model", WHISPER_STANDARD) or WHISPER_STANDARD)


def whisper_modell(standard: str, code: str | None = None) -> str:
    """Für Arabisch das genauere Modell nehmen; Deutsch und Englisch nutzen das Standardmodell."""
    code = code or {"de": "de", "en": "en", "arz": "ar"}.get(aktuell())
    if code != "ar":
        return standard
    return str(config.get("whisper_model_arabisch", WHISPER_ARABISCH) or WHISPER_ARABISCH)


def waehle(wahrscheinlichkeiten: list[tuple[str, float]]) -> str:
    """„Automatisch“: nur zwischen Deutsch und Arabisch wählen. Freie Erkennung rät bei kurzen Sätzen sonst gern
    Niederländisch oder Englisch und schreibt den Satz dann in der falschen Sprache mit."""
    werte = {code: p for code, p in wahrscheinlichkeiten if code in ("de", "ar")}
    return "ar" if werte.get("ar", 0.0) > werte.get("de", 0.0) else "de"


def waehle_mehrsprachig(wahrscheinlichkeiten: list[tuple[str, float]]) -> str:
    """Wählt die wahrscheinlichste von Jarvis' drei Gesprächssprachen; unbekannte Sprachen fallen auf Deutsch zurück."""
    werte = {code: float(p) for code, p in wahrscheinlichkeiten if code in ("de", "en", "ar")}
    return max(("de", "en", "ar"), key=lambda code: werte.get(code, -1.0))


def whisper_vorgabe(deutsch: str, code: str | None = None) -> str:
    """Vorgabetext für Whisper passend zur festgelegten oder erkannten Sprache."""
    mode = aktuell()
    if mode == "arz" or (mode == "auto" and code == "ar"):
        return VORGABE_MASRI
    if mode == "en" or (mode == "auto" and code == "en"):
        return VORGABE_ENGLISCH
    return deutsch


def stimme_fuer(text: str, stimme: str) -> str:
    """Sprachpassende Stimme wählen; benutzerdefinierte ElevenLabs-Stimmen bleiben erhalten."""
    code = text_sprache(text)
    if code == "ar":
        return str(config.get("stimme_arabisch", STIMME_ARABISCH) or STIMME_ARABISCH)
    if code == "en" and stimme.startswith("edge:") and not stimme.startswith("edge:en-"):
        return STIMME_ENGLISCH
    return stimme


def sprachcode(text: str) -> str:
    """language_code für ElevenLabs."""
    return text_sprache(text)


def fuer_sprache(text: str) -> str:
    """Sprechbare Fassung für arabischen Text: nur Markdown und Links entfernen. Die deutsche Normalisierung
    (Uhrzeiten, Beträge, Abkürzungen auf Deutsch) wäre hier falsch."""
    t = re.sub(r"https?://\S+", "الرابط", str(text or ""))
    t = re.sub(r"[*_`#]+", "", t)
    t = re.sub(r"^\s*[-•]\s+", "", t, flags=re.M)
    return re.sub(r"[ \t]{2,}", " ", t).strip()


def fuer_englisch(text: str) -> str:
    """Sprechbare englische Fassung ohne deutsche Zahlen- und Datumsnormalisierung."""
    t = re.sub(r"https?://\S+", "the link", str(text or ""))
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
             f"Sprich den Nutzer gelegentlich und natürlich mit „يا {anrede_arabisch()}“ an, nicht in jeder Antwort. "
             "Nutze kurze, idiomatische Alltagssätze und übernimm geläufige englische Fachbegriffe, wenn sie im Gespräch "
             "vorkommen. Namen, Firmen, Fachbegriffe, E-Mail-Adressen und Zahlen "
             "bleiben, wie sie sind. Inhalte für Dritte (Mails, CRM-Notizen, Termine) schreibst du in der Sprache, die der "
             "Nutzer dafür verlangt, sonst auf Deutsch.")
    if sprache == "arz":
        return "\n\n# Sprache\nAntworte immer auf Ägyptisch, auch wenn eine Nachricht auf Deutsch kommt. " + masri
    if sprache == "en":
        return ("\n\n# Language\nAnswer in natural, conversational English. Keep the user's meaning and context; don't translate unless asked. "
                "Use short, direct replies and switch language only when the user asks. Preserve names and technical terms.")
    if sprache == "auto":
        return ("\n\n# Sprache\nAntworte in der Sprache der letzten Nutzernachricht: auf Deutsch auf Deutsch, auf Arabisch "
                "immer auf Ägyptisch und auf Englisch auf Englisch. Bei gemischten Nachrichten nutze die vorherrschende "
                "Sprache und code-switche natürlich, wenn es zum Nutzer passt; übersetze nicht ungefragt. " + masri)
    return ""


def begruessung(deutsch: str) -> str:
    """Erster Satz im Echtzeit-Modus."""
    if aktuell() == "arz":
        return f"أهلاً يا {anrede_arabisch()}، كل الأنظمة شغالة. أقدر أساعدك في إيه؟"
    if aktuell() == "en":
        return f"Hello, {config.anrede() or config.get('owner_name', 'Master')}. All systems are online. What can I help with?"
    return deutsch


def echtzeit_sprache() -> str:
    """Agentensprache bei ElevenLabs; Auto startet auf Deutsch und kann per Sprachwerkzeug wechseln."""
    return {"de": "de", "en": "en", "arz": "ar", "auto": "de"}[aktuell()]
