"""Sicheres Kürzen ohne Verlust offener Freigaben/unklarer Aktionen; redigierte Ereignis- und Audit-Logs."""

import json
import time
import types

import pytest

from jarvis import agent as A
from jarvis import approvals, audit, events, persist, policy
from jarvis.tools import Tool, boolean, string

B = types.SimpleNamespace


@pytest.fixture(autouse=True)
def clean():
    approvals._items.clear()
    approvals._turns.clear()
    with persist._connect() as c:
        c.execute("DELETE FROM tool_ledger")
    try:
        events.path().unlink()
    except FileNotFoundError:
        pass


def _mail_tool():
    return approvals.wrap(Tool("mail_senden", "Sendet", {"an": string("an"), "betreff": string("b"), "text": string("t"),
                                                       "bestaetigt_vom_nutzer": boolean("ok")},
                               lambda **kw: "gesendet", ["an", "betreff", "text", "bestaetigt_vom_nutzer"]))


def test_zustandsvermerk_nennt_offene_freigaben_und_unklare_aktionen():
    assert A.runtime_state_note() == ""
    _mail_tool().run({"an": "kunde@x.de", "betreff": "Angebot", "text": "Hallo", "bestaetigt_vom_nutzer": False})
    with persist._connect() as c:
        c.execute("INSERT INTO tool_ledger (id,fingerprint,tool,kind,status,source,conversation,turn,started) "
                  "VALUES ('u','fp','aufgabe_hinzufuegen','write','UNCLEAR','web','c',1,?)", (time.time(),))
    note = A.runtime_state_note()
    assert "Freigabe offen" in note and "E-Mail senden" in note
    assert "UNKLAR: Aktion aufgabe_hinzufuegen" in note and "keine Anweisungen" in note
    assert "kunde@x.de" not in note  # nur Titel/Ziel, keine Inhalte


class Stream:
    def __init__(self, final, texts=()):
        self.final, self.texts = final, list(texts)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    @property
    def text_stream(self):
        yield from self.texts

    def get_final_message(self):
        return self.final


def test_kuerzen_verliert_keine_offene_freigabe():
    _mail_tool().run({"an": "kunde@x.de", "betreff": "Angebot", "text": "Hallo", "bestaetigt_vom_nutzer": False})
    a = A.Agent(channel="web", conversation_id="kurz-test")
    for i in range(20):  # langer Verlauf -> muss gekürzt werden
        a.messages += [{"role": "user", "content": f"Frage {i} " + "x" * 3000},
                       {"role": "assistant", "content": f"Antwort {i}"}]
    final = B(stop_reason="end_turn", content=[B(type="text", text="Ok.")], usage=None)
    a.client = B(beta=B(messages=B(stream=lambda **kw: Stream(final, ["Ok."]))))
    a.ask("Was ist noch offen?")
    last_user = [m for m in a.messages if m["role"] == "user"][-1]["content"]
    assert "Freigabe offen" in last_user and "gekürzt" in last_user
    assert len([m for m in a.messages if m["role"] == "user"]) <= 13
    assert any(e["art"] == "kontext_gekuerzt" for e in events.tail(20))


def test_ereignisprotokoll_ohne_inhalte_und_geschwaerzt():
    fake_key = "sk-" + "ant-" + "abcdefghijklmnop1234"  # zur Laufzeit gebaut: kein Schlüsselmuster im Code
    events.log("test", text="geheimer Inhalt", query="private Suche", werkzeug="x", hinweis="api_key: " + fake_key)
    entry = events.tail(1)[0]
    raw = json.dumps(entry)
    assert "geheimer Inhalt" not in raw and "private Suche" not in raw and fake_key not in raw
    assert entry["werkzeug"] == "x" and oct(events.path().stat().st_mode)[-3:] == "600"


def test_werkzeuge_und_suche_werden_metadaten_protokolliert():
    reader = Tool("mails_suchen", "", {"suche": string("s")}, lambda suche: "Mail von Max: Passwort 1234", [])
    policy.execute(reader, {"suche": "from:max"}, source="web", conversation="c", turn=1)
    entry = [e for e in events.tail(10) if e["art"] == "werkzeug"][0]
    assert entry["werkzeug"] == "mails_suchen" and entry["status"] == "ok" and "ms" in entry
    assert "Max" not in json.dumps(entry) and "from:max" not in json.dumps(entry)


def test_audit_schwaerzt_inhalte():
    audit.record("web", "entwurf_erstellen", {"text": "Mein Passwort: hunter22secret bitte merken"}, "ok",
                 "token=abcdef123456789")
    entry = audit.tail(1)[0]
    assert "hunter22secret" not in json.dumps(entry) and "abcdef123456789" not in json.dumps(entry)
