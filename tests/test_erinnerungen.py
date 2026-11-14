"""Jarvis Memory v1: nur auf Wunsch speichern, Unklares bestätigen, gezielt abrufen, ansehen, bearbeiten, löschen."""

import pytest

from jarvis import agent as A
from jarvis import erinnerungen
from jarvis.memory import MemoryStore
from helpers import B, Stream


def _agent(tmp_path, conversation="mem-v1", reply="Antwort."):
    """Agent mit eigenem Speicher; jeder Modellaufruf wird mitgezählt (Befehle dürfen das Modell nicht aufrufen)."""
    store = MemoryStore(tmp_path / "memory.sqlite3")
    a = A.Agent(channel="web", conversation_id=conversation)
    a.memory_store = store
    a.native_memory = A.NativeMemoryTool(store, on_proposal=lambda f: a._memory_commands().propose(f))
    a._restored_history = []
    calls = []

    def stream(**kw):
        calls.append(kw)
        return Stream(B(stop_reason="end_turn", content=[B(type="text", text=reply)]), [reply])

    a.client = B(beta=B(messages=B(stream=stream)))
    return a, store, calls


def _facts(store):
    return {f["content"]: f["consent"] for f in store.list_facts(100)}


@pytest.fixture(autouse=True)
def _cloud_an(monkeypatch):
    monkeypatch.delenv("JARVIS_ERINNERUNGEN_AN_CLOUD", raising=False)
    monkeypatch.setattr(erinnerungen.config, "load", lambda: {})


@pytest.mark.parametrize("text,inhalt", [
    ("Merk dir, dass ich Tee ohne Zucker trinke.", "ich Tee ohne Zucker trinke"),
    ("Merke dir bitte: Mein Steuerberater heißt Klein", "Mein Steuerberater heißt Klein"),
    ("Notier dir, dass Weber nur dienstags erreichbar ist!", "Weber nur dienstags erreichbar ist"),
])
def test_ausdruecklich_speichern_lokal_ohne_modell(tmp_path, text, inhalt):
    a, store, calls = _agent(tmp_path)
    answer = a.ask(text)
    assert answer.startswith("Gemerkt:") and calls == []  # lokal erledigt, nichts an den Anbieter
    assert _facts(store) == {inhalt: "explicit"}


def test_normales_gespraech_speichert_nichts(tmp_path):
    a, store, calls = _agent(tmp_path)
    for text in ("Ich bevorzuge kurze Antworten.", "Mein Projekt Orbit startet im Mai.", "Ich trinke immer schwarzen Kaffee."):
        a.ask(text)
    assert len(calls) == 3 and store.stats()["facts"] == 0


def test_unklares_wird_erst_nach_ja_gespeichert(tmp_path):
    a, store, calls = _agent(tmp_path)
    answer = a.ask("Merk dir, dass ich vielleicht nach Hamburg ziehe.")
    assert "Soll ich mir" in answer and calls == []
    assert _facts(store) == {"ich vielleicht nach Hamburg ziehe": "vorschlag"}
    assert erinnerungen.relevant(store, "Hamburg") == []  # unbestätigt: wird nie abgerufen
    assert a.ask("Ja").startswith("Gemerkt") and calls == []
    assert _facts(store) == {"ich vielleicht nach Hamburg ziehe": "explicit"}
    assert erinnerungen.relevant(store, "Hamburg")[0]["content"] == "ich vielleicht nach Hamburg ziehe"


def test_nein_verwirft_den_vorschlag(tmp_path):
    a, store, _ = _agent(tmp_path)
    a.ask("Merk dir, dass Weber eventuell kündigt.")
    assert "nicht" in a.ask("Nein")
    assert store.stats()["facts"] == 0


def test_rueckfrage_bleibt_offen_nur_fuer_die_naechste_kurze_antwort(tmp_path):
    a, store, calls = _agent(tmp_path)
    a.ask("Merk dir, dass ich vielleicht umziehe.")
    a.ask("Ja, und wie wird das Wetter morgen in Berlin?")  # keine Antwort auf die Rückfrage
    assert len(calls) == 1 and _facts(store)["ich vielleicht umziehe"] == "vorschlag"
    a.ask("Ja")  # Rückfrage ist inzwischen verfallen -> geht normal ans Modell
    assert len(calls) == 2 and _facts(store)["ich vielleicht umziehe"] == "vorschlag"


@pytest.mark.parametrize("text", ["Merk dir das", "Merk dir das hier.", "Merk dir"])
def test_ohne_inhalt_wird_nachgefragt_statt_geraten(tmp_path, text):
    a, store, calls = _agent(tmp_path)
    assert "Was genau soll ich mir merken" in a.ask(text)
    assert store.stats()["facts"] == 0 and calls == []


def test_widerspruch_braucht_bestaetigung(tmp_path):
    a, store, _ = _agent(tmp_path)
    a.ask("Merk dir, dass ich meinen Kaffee schwarz trinke.")
    answer = a.ask("Merk dir, dass ich meinen Kaffee mit Milch trinke.")
    assert "widerspricht" in answer
    assert _facts(store)["ich meinen Kaffee mit Milch trinke"] == "vorschlag"


def test_zugangsdaten_werden_nie_gespeichert(tmp_path):
    a, store, _ = _agent(tmp_path)
    assert "speichere ich nicht" in a.ask("Merk dir, dass mein Passwort hunter22secret ist.")
    assert store.stats()["facts"] == 0


def test_modell_kann_nur_vorschlagen(tmp_path):
    a, store, _ = _agent(tmp_path)
    result = a.native_memory.call("wissen_speichern", {"content": "Bevorzugt Termine am Vormittag", "category": "Vorliebe"})
    assert result["status"].startswith("NUR VORSCHLAG")
    assert _facts(store) == {"Bevorzugt Termine am Vormittag": "vorschlag"}
    assert store.search("Termine Vormittag", 5)["facts"] == []
    a.ask("Ja")  # Antwort auf „Soll ich mir das merken?“
    assert _facts(store) == {"Bevorzugt Termine am Vormittag": "explicit"}


def test_ansehen_einzeln_und_alle_loeschen_mit_bestaetigung(tmp_path):
    a, store, calls = _agent(tmp_path)
    a.ask("Merk dir, dass ich Tee trinke.")
    a.ask("Merk dir, dass Weber dienstags erreichbar ist.")
    listing = a.ask("Zeig meine Erinnerungen")
    assert "1. Weber dienstags erreichbar ist" in listing and "2. ich Tee trinke" in listing
    assert "Soll ich" in a.ask("Lösche Erinnerung 2")
    assert store.stats()["facts"] == 2  # erst nach Ja
    assert a.ask("Ja") == "Gelöscht."
    assert list(_facts(store)) == ["Weber dienstags erreichbar ist"]
    assert "Soll ich wirklich alle 1 Erinnerungen" in a.ask("Lösche alle Erinnerungen")
    assert "nichts gelöscht" in a.ask("Nein") and store.stats()["facts"] == 1
    a.ask("Lösche alle Erinnerungen")
    assert "Alle 1 Erinnerungen sind gelöscht" in a.ask("Ja, alle löschen")
    assert store.stats()["facts"] == 0
    assert store.stats()["episodes"] > 0  # Chatverlauf ist eine eigene Speicherart und bleibt
    assert calls == []


# --- Regression P1: Ablehnung oder Mehrdeutiges darf nie als Ja gelten -------------------------------------------------

def _bestand(store):
    """Zeilenzahlen direkt aus der Datenbank – unabhängig von den Zählern der Speicherklasse."""
    import sqlite3
    c = sqlite3.connect(store.path)
    try:
        return tuple(c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("facts", "fact_versions"))
    finally:
        c.close()


def _mit_zwei_erinnerungen(tmp_path):
    a, store, calls = _agent(tmp_path)
    a.ask("Merk dir, dass ich Tee trinke.")
    a.ask("Merk dir, dass Weber dienstags erreichbar ist.")
    fact = store.list_facts(1)[0]
    store.update_fact(fact["id"], fact["content"] + " (korrigiert)")  # erzeugt eine Zeile in fact_versions
    assert _bestand(store)[0] == 2 and _bestand(store)[1] >= 1
    return a, store, calls


ABLEHNEND_ODER_UNKLAR = [
    "Ja, aber bitte nicht", "Ja, nicht löschen", "ja nicht", "JA, ABER NICHT!", "Ja... lieber nicht.", "Ja, nein", "Jein",
    "Ja, warte", "Ja, aber erst später", "Ja vielleicht", "Ok, doch nicht", "Okay, abbrechen", "Genau, nicht löschen!",
    "Nein", "nein", "NEIN!", "Nein.", "Nein, danke", "Nee", "Nö", "Bitte nicht", "Bitte nicht löschen!", "Nicht löschen",
    "Lieber nicht", "Stopp", "Abbrechen", "Vergiss es", "Auf keinen Fall", "Niemals",
]
EINDEUTIG_JA = ["Ja", "ja", "JA", "Ja.", "Ja!", " ja ", "Ja, bitte", "Ja bitte!", "Ja, löschen", "Ok", "Okay.", "Genau", "Jawohl!",
                "Ja, mach das", "Ja, gerne"]


@pytest.mark.parametrize("antwort", ABLEHNEND_ODER_UNKLAR)
def test_einzelloeschung_loescht_nie_bei_ablehnung_oder_zweifel(tmp_path, antwort):
    a, store, calls = _mit_zwei_erinnerungen(tmp_path)
    a.ask("Zeig meine Erinnerungen")
    assert "endgültig löschen" in a.ask("Lösche Erinnerung 1")
    vorher = _bestand(store)
    reply = a.ask(antwort)
    assert _bestand(store) == vorher and "nichts gelöscht" in reply and calls == []


@pytest.mark.parametrize("antwort", ABLEHNEND_ODER_UNKLAR + ["Ja", "Ja!", "Ok", "Ja, bitte", "Ja, löschen", "Ja, mach das"])
def test_alle_loeschen_loescht_nie_bei_ablehnung_zweifel_oder_blossem_ja(tmp_path, antwort):
    a, store, calls = _mit_zwei_erinnerungen(tmp_path)
    assert erinnerungen.ALLE_LOESCHEN_SATZ in a.ask("Lösche alle Erinnerungen")
    vorher = _bestand(store)
    reply = a.ask(antwort)
    assert _bestand(store) == vorher and "nichts gelöscht" in reply and calls == []


@pytest.mark.parametrize("antwort", EINDEUTIG_JA)
def test_einzelloeschung_nach_eindeutigem_ja(tmp_path, antwort):
    a, store, _ = _mit_zwei_erinnerungen(tmp_path)
    a.ask("Zeig meine Erinnerungen")
    a.ask("Lösche Erinnerung 1")
    assert a.ask(antwort) == "Gelöscht." and _bestand(store)[0] == 1


@pytest.mark.parametrize("antwort", ["Ja, alle löschen", "ja alle löschen", "JA, ALLE LÖSCHEN!", "Ja, bitte alle Erinnerungen löschen.",
                                     "Ja, lösche alle"])
def test_alle_loeschen_nur_mit_ausdruecklichem_satz(tmp_path, antwort):
    a, store, _ = _mit_zwei_erinnerungen(tmp_path)
    a.ask("Lösche alle Erinnerungen")
    assert "Alle 2 Erinnerungen sind gelöscht" in a.ask(antwort) and _bestand(store) == (0, 0)


def test_mehrdeutige_antwort_haelt_die_rueckfrage_offen_und_fragt_erneut(tmp_path):
    a, store, calls = _mit_zwei_erinnerungen(tmp_path)
    a.ask("Zeig meine Erinnerungen")
    a.ask("Lösche Erinnerung 1")
    reply = a.ask("Ja, aber bitte nicht")
    assert "nicht eindeutig" in reply and "Antworte nur mit" in reply and _bestand(store)[0] == 2
    assert a.ask("Ja") == "Gelöscht." and _bestand(store)[0] == 1  # erst die eindeutige Antwort wirkt
    a.ask("Lösche alle Erinnerungen")
    assert "nichts gelöscht" in a.ask("Ja") and _bestand(store)[0] == 1
    assert "nichts gelöscht" in a.ask("Nein") and _bestand(store)[0] == 1
    assert calls == []


def test_bestaetigung_gilt_nur_fuer_genau_diese_aktion(tmp_path, monkeypatch):
    a, store, calls = _mit_zwei_erinnerungen(tmp_path)
    # „Ja, alle löschen“ bestätigt keine Einzellöschung
    a.ask("Zeig meine Erinnerungen")
    a.ask("Lösche Erinnerung 1")
    assert "nichts gelöscht" in a.ask("Ja, alle löschen") and _bestand(store)[0] == 2
    a.ask("Nein")
    # Ändert sich der Bestand nach der Rückfrage, gilt die Bestätigung nicht mehr
    assert "alle 2 Erinnerungen" in a.ask("Lösche alle Erinnerungen")
    store.remember("Neue Erinnerung nach der Rückfrage", "note", "user")
    reply = a.ask("Ja, alle löschen")
    assert "Bestand hat sich geändert" in reply and _bestand(store)[0] == 3
    # Eine verfallene Rückfrage löscht nichts mehr
    a.ask("Nein")
    a.ask("Lösche alle Erinnerungen")
    echt = erinnerungen.time.monotonic()
    monkeypatch.setattr(erinnerungen.time, "monotonic", lambda: echt + erinnerungen.RUECKFRAGE_GILT_S + 1)
    a.ask("Ja, alle löschen")
    assert _bestand(store)[0] == 3


def test_neuer_befehl_ist_keine_antwort_auf_die_rueckfrage(tmp_path):
    a, store, _ = _agent(tmp_path)
    a.ask("Merk dir, dass ich vielleicht umziehe.")
    assert a.ask("Merk dir, dass ich nicht rauche.").startswith("Gemerkt")
    assert _facts(store) == {"ich vielleicht umziehe": "vorschlag", "ich nicht rauche": "explicit"}


@pytest.mark.parametrize("antwort", ["Ja, aber bitte nicht", "Ja, nicht speichern", "Nein"])
def test_vorschlag_wird_bei_ablehnung_oder_zweifel_nicht_bestaetigt(tmp_path, antwort):
    a, store, _ = _agent(tmp_path)
    a.ask("Merk dir, dass ich vielleicht nach Hamburg ziehe.")
    a.ask(antwort)
    assert "explicit" not in _facts(store).values()


def test_vergiss_dass_findet_und_fragt_nach(tmp_path):
    a, store, _ = _agent(tmp_path)
    a.ask("Merk dir, dass mein Lieblingsitaliener Da Mario ist.")
    assert "Da Mario" in a.ask("Vergiss, dass mein Lieblingsitaliener Da Mario ist")
    a.ask("ja")
    assert store.stats()["facts"] == 0


def test_bearbeiten_und_wiederfinden_nach_neustart(tmp_path):
    a, store, _ = _agent(tmp_path)
    a.ask("Merk dir, dass mein Hauptkunde die Kanzlei Weber ist.")
    fact = store.list_facts(1)[0]
    store.update_fact(fact["id"], "Mein Hauptkunde ist die Kanzlei Weber & Partner.")
    # „Neustart“: neuer Speicher-Prozess und neuer Agent auf derselben Datei
    reopened = MemoryStore(tmp_path / "memory.sqlite3")
    assert reopened.get_fact(fact["id"])["content"] == "Mein Hauptkunde ist die Kanzlei Weber & Partner."
    b = A.Agent(channel="web", conversation_id="nach-neustart")
    b.memory_store = reopened
    b._restored_history = []
    b._append_user_turn("Wer ist mein Hauptkunde?")
    assert "Kanzlei Weber & Partner" in b.messages[-1]["content"]
    assert b._used_memories == [{"id": fact["id"], "inhalt": "Mein Hauptkunde ist die Kanzlei Weber & Partner."}]


def test_nur_passende_erinnerungen_hoechstens_fuenf(tmp_path):
    a, store, _ = _agent(tmp_path)
    for i in range(8):
        store.remember(f"Projekt Orbit Meilenstein {i} ist geplant", "note", "user")
    store.remember("Lieblingsfarbe ist Blau", "note", "user")
    a._append_user_turn("Wie steht es um Projekt Orbit?")
    turn = a.messages[-1]["content"]
    assert turn.count("Meilenstein") == erinnerungen.MAX_RECALL and "Blau" not in turn
    a.messages.append({"role": "assistant", "content": "Gut."})
    a._append_user_turn("Hallo")
    assert "Meilenstein" not in a.messages[-1]["content"] and a._used_memories == []


def test_cloud_schalter_haelt_erinnerungen_zurueck(tmp_path, monkeypatch):
    a, store, calls = _agent(tmp_path)
    store.remember("Mein Hauptkunde ist die Kanzlei Weber", "note", "user")
    monkeypatch.setenv("JARVIS_ERINNERUNGEN_AN_CLOUD", "aus")
    a.ask("Wer ist mein Hauptkunde?")
    sent = str(calls[-1]["messages"])
    assert "Kanzlei Weber" not in sent and a._used_memories == []
    assert a.native_memory.call("wissen_suchen", {"query": "Hauptkunde"}) is not None
    a.native_memory.allowed = lambda: erinnerungen.cloud_allowed(a.provider.id)
    assert a.native_memory.call("wissen_suchen", {"query": "Hauptkunde"})["facts"] == []
    # lokal beantwortete Befehle: Inhalt landet nicht im Verlauf, der später an die Cloud geht
    a.ask("Merk dir, dass ich Tee trinke.")
    assert "Tee" not in str(a.messages[-2:])
    # Ollama läuft lokal: dort dürfen passende Erinnerungen weiter mit
    assert erinnerungen.cloud_allowed("ollama") is True and erinnerungen.cloud_allowed("anthropic") is False


def test_einstellung_wird_gespeichert(monkeypatch):
    from jarvis import echtzeit

    saved = {}
    monkeypatch.setattr(echtzeit.config, "save", lambda k, v: saved.__setitem__(k, v))
    monkeypatch.setattr(echtzeit, "status", lambda model_id=None: {})
    echtzeit.save_settings({"erinnerungen_an_cloud": False})
    assert saved == {"erinnerungen_an_cloud": "aus"}
    with pytest.raises(ValueError):
        echtzeit.save_settings({"erinnerungen_an_cloud": "vielleicht"})


TOKEN = "offline-unit-test-bridge-credential-only"
AUTH = {"Authorization": "Bearer " + TOKEN}


@pytest.mark.asyncio
async def test_bruecke_alle_loeschen_nur_mit_bestaetigung(tmp_path, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    from jarvis import memory, v3_bridge

    store = MemoryStore(tmp_path / "memory.sqlite3")
    store.remember("Ich trinke Tee", "note", "user")
    monkeypatch.setattr(memory, "store", lambda *a, **k: store)
    client = TestClient(TestServer(v3_bridge.create_app(TOKEN, demo=False)))
    await client.start_server()
    try:
        r = await client.post("/internal/gedaechtnis/alle_loeschen", json={}, headers=AUTH)
        assert r.status == 400 and store.stats()["facts"] == 1
        r = await client.post("/internal/gedaechtnis/alle_loeschen", json={"bestaetigt": True}, headers=AUTH)
        assert (await r.json())["geloescht"] == 1 and store.stats()["facts"] == 0
    finally:
        await client.close()
