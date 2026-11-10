"""Intelligentes Unterbrechen: Regeln + Modell-Einschaetzung, im Zweifel weiterreden."""

import types

from jarvis import agent, bargein

B = types.SimpleNamespace


def test_regeln():
    assert bargein.quick_rules("Stopp") is True
    assert bargein.quick_rules("Moment, schreib lieber an Herrn Meier") is True
    assert bargein.quick_rules("Jarvis, nimm den Kalender von morgen") is True
    assert bargein.quick_rules("mhm") is False and bargein.quick_rules("ja genau") is False
    assert bargein.quick_rules("ok gut") is False                       # zu kurz
    assert bargein.quick_rules("Kannst du mir das Salz geben") is None  # -> Modell entscheidet


def fake_client(answer):
    calls = []

    def create(**kw):
        calls.append(kw)
        return B(content=[B(type="text", text=answer)])

    return B(messages=B(create=create)), calls


def test_modell_entscheidet(monkeypatch):
    c, calls = fake_client('{"unterbrechen": false}')
    monkeypatch.setattr(agent, "make_client", lambda timeout=60.0: c)
    r = bargein.should_interrupt("Schatz, hast du die Schlüssel gesehen", "Du hast heute drei Termine")
    assert r == {"unterbrechen": False, "grund": "modell"}
    assert "Schlüssel" in calls[0]["messages"][0]["content"] and calls[0]["max_tokens"] <= 20
    c, _ = fake_client('{"unterbrechen": true}')
    monkeypatch.setattr(agent, "make_client", lambda timeout=60.0: c)
    assert bargein.should_interrupt("Nein, ich meinte den Termin am Donnerstag", "Am Mittwoch hast du")["unterbrechen"]


def test_im_zweifel_weiterreden(monkeypatch):
    def boom(timeout=60.0):
        raise RuntimeError("kein Netz")
    monkeypatch.setattr(agent, "make_client", boom)
    assert bargein.should_interrupt("Was kostet das eigentlich alles zusammen", "")["unterbrechen"] is False
