"""Dein Profil: eine Textdatei (~/.jarvis/profil.md), die Jarvis bei jedem Start liest."""

from __future__ import annotations

from . import config

TEMPLATE = """# Profil von Omar
- Wohnort: Muenchen
- Beruf: selbststaendig im B2B- und B2C-Vertrieb
- Sprache: Deutsch, Anrede 'du'. Direkt, ungeschoent, ergebnisorientiert – kein Ja-Sager.
- Antworten kurz und praxisnah. Erst handeln, dann erklaeren.
- Mails an Kunden: freundlich, direkt, professionell, ohne Floskeln.
- Wichtige Kontakte / Kunden: (hier eintragen)
"""


def path():
    return config.data_dir() / "profil.md"


def read() -> str:
    p = path()
    if not p.exists():
        p.write_text(TEMPLATE, encoding="utf-8")
    return p.read_text(encoding="utf-8").strip()
