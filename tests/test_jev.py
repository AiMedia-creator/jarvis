"""Jev-Werkzeug (systemone_pruefen): nur mit Opt-in und Schlüssel, Fehler nie als Antwort."""

import pytest

from jarvis import jev
from jarvis.tools import ToolFailure


def namen(monkeypatch):
    from jarvis.tools import all_tools

    return {t.name for t in all_tools({})}


def test_standardmaessig_aus_und_im_werkzeugkasten_unsichtbar(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts_test")
    monkeypatch.delenv("JARVIS_JEV", raising=False)
    assert not jev.enabled() and jev.tools() == []
    assert "systemone_pruefen" not in namen(monkeypatch)


def test_mit_opt_in_sichtbar(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts_test")
    monkeypatch.setenv("JARVIS_JEV", "an")
    assert "systemone_pruefen" in namen(monkeypatch)


def test_ohne_opt_in_verweigert(monkeypatch):
    monkeypatch.delenv("JARVIS_JEV", raising=False)
    with pytest.raises(ToolFailure):
        jev.pruefen("Text", "Ist das ein Test?")
