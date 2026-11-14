"""Autonomer Operator (n8n), Business-Coach und Dauer-Gedächtnis."""

import pytest

from jarvis import agent as A
from jarvis import policy
from jarvis.memory import MemoryStore


def test_dauer_fakten_nur_wenn_sie_zur_frage_passen(tmp_path):
    m = MemoryStore(tmp_path / "m.sqlite3")
    m.remember("Bewerbungen immer auf Englisch und unter 150 Woertern", category="vorliebe", source="user")
    m.remember("Firma: KI-Automatisierung fuer Steuerberater in Baden-Wuerttemberg", category="unternehmen", source="user")
    m.remember("Bei Steuerberatern nie mit Kostenersparnis eroeffnen, sondern mit Datenschutz", category="feedback", source="user")
    m.remember("Einkaufsliste: Milch", category="note", source="user")
    assert {f["category"] for f in m.standing()} == {"preference", "firma", "lektion"}
    a = A.Agent(channel="web", conversation_id="mem-test")
    a.memory_store = m
    # Memory v1: nur passende Erinnerungen, nie der ganze Speicher
    text = a._memory_text("Wie wird das Wetter morgen?")  # Frage ohne Bezug
    assert "Woertern" not in text and "Steuerberater" not in text and "Milch" not in text
    text = a._memory_text("Schreib eine Bewerbung auf Englisch")
    assert "unter 150 Woertern" in text and "Milch" not in text
    p = A.SYSTEM_PROMPT
    assert '"lektion"' in p and "Soll ich mir das generell merken?" in p and "# Autonomer Operator und Business-Coach" in p
