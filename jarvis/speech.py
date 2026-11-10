"""Deterministische deutsche Aussprache-Normalisierung NUR für die Sprachausgabe.

Der angezeigte/gespeicherte Text bleibt unverändert; nur die Vertonung bekommt eine sprechbare Fassung:
Uhrzeiten, Datumsangaben, Geldbeträge, Prozent, Telefonnummern, Abkürzungen, Links, Markdown-Reste und ein
erweiterbares Aussprache-Lexikon für Namen und Firmen (~/.jarvis/aussprache.json, z. B. {"Guliver": "Gulliwer"}).
Keine KI, keine Rateversuche: Was nicht eindeutig passt, bleibt wie es ist.
"""

from __future__ import annotations

import json
import re

from . import config

MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
          "November", "Dezember"]
ABBREVIATIONS = [
    (r"z\.\s?B\.", "zum Beispiel"), (r"d\.\s?h\.", "das heißt"), (r"u\.\s?a\.", "unter anderem"),
    (r"bzw\.", "beziehungsweise"), (r"ca\.", "circa"), (r"inkl\.", "inklusive"), (r"exkl\.", "exklusive"),
    (r"ggf\.", "gegebenenfalls"), (r"usw\.", "und so weiter"), (r"evtl\.", "eventuell"), (r"vgl\.", "vergleiche"),
    (r"Nr\.", "Nummer"), (r"Tel\.", "Telefon"), (r"Std\.", "Stunden"), (r"Min\.", "Minuten"), (r"Sek\.", "Sekunden"),
    (r"Mio\.", "Millionen"), (r"Mrd\.", "Milliarden"), (r"Tsd\.", "Tausend"), (r"max\.", "maximal"),
    (r"min\.", "mindestens"), (r"KW\s?(?=\d)", "Kalenderwoche "), (r"Dr\.", "Doktor"), (r"Prof\.", "Professor"),
]
# Eingebaute Aussprachen (über aussprache.json erweiterbar/überschreibbar)
LEXICON = {"HubSpot": "Habspott", "Klaviyo": "Klavijo", "n8n": "N acht N", "Upwork": "Apwörk",
           "LinkedIn": "Linkdin", "ElevenLabs": "Ileven Labs", "CRM": "C R M", "ROI": "R O I", "KI": "K I",
           "SaaS": "Sass", "KPI": "K P I", "CEO": "C E O", "DSGVO": "D S G V O", "AVV": "A V V", "ETA": "E T A"}
_CURRENCY = {"€": "Euro", "EUR": "Euro", "$": "Dollar", "USD": "Dollar", "£": "Pfund", "GBP": "Pfund",
             "CHF": "Franken"}
_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d{2})?(?=\s?(?:USD|GBP|\$|£)|(?<=[$£]\d)|\b)|\d{1,3}(?:[.\s]\d{3})+(?:,\d+)?|\d+(?:,\d+)?"


def _lexicon() -> dict:
    lex = dict(LEXICON)
    try:
        extra = json.loads((config.data_dir() / "aussprache.json").read_text(encoding="utf-8"))
        if isinstance(extra, dict):
            lex.update({str(k): str(v) for k, v in extra.items() if k and len(str(k)) <= 60})
    except (OSError, ValueError):
        pass
    return lex


def _plain_number(value: str) -> str:
    """'1.500' / '1 500' -> '1500' (Tausenderpunkt weg), Komma bleibt (wird als 'Komma' gesprochen)."""
    return re.sub(r"(?<=\d)[.\s](?=\d{3}\b)", "", value)


def _money(match: re.Match) -> str:
    sign_before, amount, scale, sign_after = match.group("pre"), match.group("amt"), match.group("scale"), match.group("post")
    currency = _CURRENCY.get((sign_before or sign_after or "").strip(), "")
    if currency in ("Dollar", "Pfund") and re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d{2})?", amount):
        amount = amount.replace(",", "").split(".")[0]  # englische Schreibweise: 3,000 = dreitausend
    amount = _plain_number(amount)
    scale_word = {"Mio.": " Millionen", "Mio": " Millionen", "Mrd.": " Milliarden", "Mrd": " Milliarden",
                  "Tsd.": " Tausend", "k": " Tausend"}.get((scale or "").strip(), "")
    if not scale_word and "," in amount:
        euros, cents = amount.split(",", 1)
        if len(cents) == 2 and currency:
            return f"{euros} {currency} {int(cents)}" if int(cents) else f"{euros} {currency}"
    return f"{amount}{scale_word} {currency}".strip()


def _date(day: str, month: str, year: str | None) -> str:
    m = int(month)
    if not 1 <= m <= 12 or not 1 <= int(day) <= 31:
        return f"{day}.{month}." + (year or "")
    return f"{int(day)}. {MONTHS[m - 1]}" + (f" {year}" if year else "")


def _phone(match: re.Match) -> str:
    raw = match.group(0)
    digits = re.sub(r"[^\d+]", "", raw)
    if len(re.sub(r"\D", "", digits)) < 6:
        return raw
    groups = [g for g in re.split(r"[\s/()-]+", raw.strip()) if g]
    spoken = []
    for g in groups:
        prefix = "plus " if g.startswith("+") else ""
        digits_only = g.lstrip("+")
        spoken.append(prefix + " ".join(digits_only) if digits_only.isdigit() else g)
    return ", ".join(spoken)


def for_speech(text: str) -> str:
    t = str(text or "")
    # Markdown-Reste und Links
    t = re.sub(r"https?://\S+", "der Link", t)
    t = re.sub(r"[*_`#]+", "", t)
    t = re.sub(r"^\s*[-•]\s+", "", t, flags=re.M)
    # Telefonnummern (mit Kontext "Tel"/"+"/"0" und mindestens 6 Ziffern)
    t = re.sub(r"(?<![\w.,])(?:\+\d{1,3}\s?)?\(?0?\d{2,5}\)?(?:\s?[/-]\s?|\s)\d{3,}(?:(?:\s?[/-]\s?|\s)\d{2,})*(?![\w,])",
               _phone, t)
    # Datum: ISO, dd.mm.yyyy, dd.mm.
    t = re.sub(r"\b(\d{4})-(\d{2})-(\d{2})\b", lambda m: _date(m.group(3), m.group(2), m.group(1)), t)
    t = re.sub(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b", lambda m: _date(m.group(1), m.group(2), m.group(3)), t)
    t = re.sub(r"\b(\d{1,2})\.(\d{1,2})\.(?!\d)", lambda m: _date(m.group(1), m.group(2), None), t)
    # Uhrzeit: 14:30 / 9.30 Uhr / 14:00
    t = re.sub(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\s?Uhr\b", lambda m: f"{int(m.group(1))} Uhr"
               + (f" {int(m.group(2))}" if int(m.group(2)) else ""), t)
    t = re.sub(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", lambda m: f"{int(m.group(1))} Uhr"
               + (f" {int(m.group(2))}" if int(m.group(2)) else ""), t)
    # Geld (vor/nach dem Betrag, mit Mio./Mrd./k)
    t = re.sub(rf"(?P<pre>[€$£]\s?)(?P<amt>{_NUM})(?P<scale>\s?(?:Mio\.?|Mrd\.?|Tsd\.|k)\b)?(?P<post>)", _money, t)
    t = re.sub(rf"(?P<pre>)(?P<amt>{_NUM})(?P<scale>\s?(?:Mio\.?|Mrd\.?|Tsd\.|k))?\s?(?P<post>€|EUR\b|USD\b|\$|£|GBP\b|CHF\b)",
               _money, t)
    t = re.sub(r"(\d)\s?%", r"\1 Prozent", t)
    # Abkürzungen
    for pattern, spoken in ABBREVIATIONS:
        t = re.sub(rf"(?<![\w]){pattern}", spoken, t)
    # Lexikon (ganze Wörter, längste zuerst)
    for word, spoken in sorted(_lexicon().items(), key=lambda kv: -len(kv[0])):
        t = re.sub(rf"(?<![\w]){re.escape(word)}(?![\w])", spoken, t)
    return re.sub(r"[ \t]{2,}", " ", t).strip()
