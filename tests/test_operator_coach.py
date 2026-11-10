"""Autonomer Operator (n8n), Business-Coach und Dauer-Gedächtnis."""

import pytest

from jarvis import agent as A
from jarvis import policy
from jarvis.memory import MemoryStore


def test_dauer_fakten_stehen_immer_im_gedaechtnis(tmp_path):
    m = MemoryStore(tmp_path / "m.sqlite3")
    m.remember("Bewerbungen immer auf Englisch und unter 150 Woertern", category="vorliebe", source="user")
    m.remember("Firma: KI-Automatisierung fuer Steuerberater in Baden-Wuerttemberg", category="unternehmen", source="user")
    m.remember("Bei Steuerberatern nie mit Kostenersparnis eroeffnen, sondern mit Datenschutz", category="feedback", source="user")
    m.remember("Einkaufsliste: Milch", category="note", source="user")
    assert {f["category"] for f in m.standing()} == {"preference", "firma", "lektion"}
    a = A.Agent(channel="web", conversation_id="mem-test")
    a.memory_store = m
    text = a._memory_text("Wie wird das Wetter morgen?")  # Frage ohne Bezug
    assert "unter 150 Woertern" in text and "Steuerberater in Baden" in text and "Datenschutz" in text
    p = A.SYSTEM_PROMPT
    assert '"lektion"' in p and "Soll ich mir das generell merken?" in p and "# Autonomer Operator und Business-Coach" in p
