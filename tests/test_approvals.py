import pytest

from jarvis import approvals, audit
from jarvis.tools import Tool, ToolFailure, boolean, string

sent = []


def _send(an, betreff, text, bestaetigt_vom_nutzer):
    if not bestaetigt_vom_nutzer:
        raise ToolFailure("abgelehnt")
    sent.append((an, betreff))
    return f"Mail an {an} gesendet."


def tool():
    return approvals.wrap(Tool("mail_senden", "Sendet", {"an": string("an"), "betreff": string("b"), "text": string("t"),
                                                       "bestaetigt_vom_nutzer": boolean("ok")}, _send,
                               ["an", "betreff", "text", "bestaetigt_vom_nutzer"]))


ARGS = {"an": "max@x.de", "betreff": "Angebot", "text": "Hallo Max"}


@pytest.fixture(autouse=True)
def clean():
    approvals._items.clear()
    sent.clear()


def test_ohne_vorlage_kein_versand():
    with pytest.raises(ToolFailure, match="Vorlage"):
        tool().run({**ARGS, "bestaetigt_vom_nutzer": True})
    assert sent == []


def test_vorlage_dann_ja_dann_kein_duplikat():
    out = tool().run({**ARGS, "bestaetigt_vom_nutzer": False})
    assert "VORLAGE" in out and sent == []
    item = approvals.listing()[0]
    assert item["status"] == "WAITING_FOR_APPROVAL" and {"feld": "An", "wert": "max@x.de"} in item["vorschau"]["zeilen"]
    assert "gesendet" in tool().run({**ARGS, "bestaetigt_vom_nutzer": True})
    assert sent == [("max@x.de", "Angebot")]
    with pytest.raises(ToolFailure, match="bereits"):
        tool().run({**ARGS, "bestaetigt_vom_nutzer": True})
    assert len(sent) == 1


def test_geaenderte_angaben_brauchen_neue_vorlage():
    tool().run({**ARGS, "bestaetigt_vom_nutzer": False})
    with pytest.raises(ToolFailure):
        tool().run({**ARGS, "text": "Anderer Text", "bestaetigt_vom_nutzer": True})
    assert sent == []


def test_klick_freigabe_und_hinweis():
    notes = []
    approvals.on_decision(notes.append)
    tool().run({**ARGS, "bestaetigt_vom_nutzer": False})
    item = approvals.listing()[0]
    assert "gesendet" in approvals.approve(item["id"])
    assert approvals.listing()[0]["status"] == "COMPLETED" and notes and "Klick" in notes[-1]
    assert audit.tail(1)[0]["quelle"] == "klick"
    with pytest.raises(ToolFailure):
        approvals.approve(item["id"])  # kein zweites Mal
    assert len(sent) == 1
    approvals._listeners.remove(notes.append)


def test_verwerfen_und_ablauf(monkeypatch):
    tool().run({**ARGS, "bestaetigt_vom_nutzer": False})
    item = approvals.listing()[0]
    approvals.reject(item["id"])
    assert approvals.listing()[0]["status"] == "CANCELLED"
    with pytest.raises(ToolFailure):
        tool().run({**ARGS, "bestaetigt_vom_nutzer": True})
    tool().run({**ARGS, "betreff": "Neu", "bestaetigt_vom_nutzer": False})
    monkeypatch.setattr(approvals, "TTL", -1)
    assert approvals.listing()[0]["status"] == "CANCELLED"


def test_alle_schreibenden_werkzeuge_sind_geschuetzt(monkeypatch):
    monkeypatch.setenv("HUBSPOT_TOKEN", "tok")
    from jarvis.tools import all_tools

    tools = {t.name: t for t in all_tools({})}
    for name in ("hubspot_notiz_erstellen", "hubspot_aufgabe_erstellen", "hubspot_mail_senden", "hubspot_mail_protokollieren"):
        assert "Vorlage" in tools[name].description
