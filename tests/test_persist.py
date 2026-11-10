"""Jobs und Freigaben ueberleben einen Neustart; was beim Neustart lief, wird nie automatisch wiederholt."""

import sqlite3
import time

import pytest

from jarvis import approvals, jobs, persist
from jarvis.tools import Tool, ToolFailure, boolean, string

sent: list = []


def _send(an, betreff, text, bestaetigt_vom_nutzer):
    sent.append((an, betreff))
    return f"Mail an {an} gesendet."


def mail_tool():
    return approvals.wrap(Tool("mail_senden", "Sendet", {"an": string("an"), "betreff": string("b"), "text": string("t"),
                                                       "bestaetigt_vom_nutzer": boolean("ok")}, _send,
                               ["an", "betreff", "text", "bestaetigt_vom_nutzer"]))


ARGS = {"an": "kunde@x.de", "betreff": "Rechnung", "text": "Anbei die Rechnung."}


def restart():
    """Simuliert einen Prozess-Neustart: Arbeitsspeicher leer, Datenbank bleibt."""
    jobs._jobs.clear()
    approvals._items.clear()
    approvals._turns.clear()
    return persist.restore(force=True)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.delenv("JARVIS_FREIGABE_NUR_KLICK", raising=False)
    with persist._connect() as c:
        c.execute("DELETE FROM jobs")
        c.execute("DELETE FROM approvals")
    jobs._jobs.clear()
    approvals._items.clear()
    approvals._turns.clear()
    sent.clear()
    yield


def test_laufender_job_wird_nach_neustart_failed_nicht_fortgesetzt():
    job = jobs.create("Analysiere Posteingang")
    step = jobs.add_step(job, "mails_suchen", {"suche": "in:inbox"}, "lesen")
    done = jobs.create("Fertiger Auftrag")
    jobs.set_result(done, "Bericht")
    jobs.finish(done, jobs.COMPLETED)

    assert restart()["jobs"] == 2
    again = jobs.get(job["id"])
    assert again["status"] == jobs.FAILED and again["fehler"] == jobs.RESTART_REASON
    assert again["schritte"][0]["id"] == step["id"] and again["schritte"][0]["status"] == jobs.FAILED
    finished = jobs.get(done["id"])
    assert finished["status"] == jobs.COMPLETED and finished["ergebnis"] == "Bericht"
    # Der neue Status ist selbst gespeichert: ein zweiter Neustart aendert nichts mehr
    restart()
    assert jobs.get(job["id"])["status"] == jobs.FAILED
    assert [j["id"] for j in jobs.recent()][:2] == [done["id"], job["id"]]


def test_offene_vorlage_ueberlebt_neustart_und_ist_dann_freigebbar():
    t = mail_tool()
    with approvals.context("haupt", approvals.next_turn("haupt"), "web"):
        t.run({**ARGS, "bestaetigt_vom_nutzer": False})
    item = approvals.listing()[0]["id"]

    restart()
    listed = approvals.listing()
    assert listed[0]["id"] == item and listed[0]["status"] == "WAITING_FOR_APPROVAL"
    # Zaehler beginnt nach dem Neustart wieder bei 1 - die alte Vorlage stammt trotzdem aus einem frueheren Zug.
    with approvals.context("haupt", approvals.next_turn("haupt"), "web"):
        assert "gesendet" in t.run({**ARGS, "bestaetigt_vom_nutzer": True})
    assert sent == [("kunde@x.de", "Rechnung")]
    restart()
    assert approvals.listing()[0]["status"] == "COMPLETED"
    with pytest.raises(ToolFailure):
        approvals.approve(item)
    assert len(sent) == 1


def test_klick_nach_neustart_nutzt_registrierten_handler():
    mail_tool().run({**ARGS, "bestaetigt_vom_nutzer": False})
    item = approvals.listing()[0]["id"]
    restart()
    mail_tool()  # Werkzeuge werden beim Start neu geladen und registrieren ihren Handler
    assert "gesendet" in approvals.approve(item)
    assert len(sent) == 1


def test_ausfuehrung_beim_absturz_wird_unklar_und_nie_wiederholt():
    mail_tool().run({**ARGS, "bestaetigt_vom_nutzer": False})
    item = approvals._items[approvals.listing()[0]["id"]]
    # Absturz mitten im Versand: RUNNING ist gespeichert, das Ergebnis nicht mehr
    item["status"] = "RUNNING"
    approvals._save(item)

    restart()
    listed = approvals.listing()[0]
    assert listed["status"] == approvals.UNCLEAR and "prüfen" in listed["status_text"]
    with pytest.raises(ToolFailure, match="unklar"):
        approvals.approve(item["id"])
    with approvals.context("haupt", approvals.next_turn("haupt"), "web"):
        with pytest.raises(ToolFailure):
            mail_tool().run({**ARGS, "bestaetigt_vom_nutzer": True})
    assert sent == []


def test_abgelaufene_vorlage_bleibt_nach_neustart_zu():
    mail_tool().run({**ARGS, "bestaetigt_vom_nutzer": False})
    item = approvals._items[approvals.listing()[0]["id"]]
    item["erstellt"] = time.time() - approvals.TTL - 5
    approvals._save(item)
    restart()
    assert approvals.listing()[0]["status"] == "CANCELLED"
    with pytest.raises(ToolFailure):
        approvals.approve(item["id"])


def test_verworfen_ist_gespeichert():
    mail_tool().run({**ARGS, "bestaetigt_vom_nutzer": False})
    item = approvals.listing()[0]["id"]
    approvals.reject(item)
    restart()
    assert approvals.listing()[0]["status"] == "CANCELLED"


def test_speicherfehler_stoppt_jarvis_nicht(monkeypatch, tmp_path, capsys):
    blocked = tmp_path / "kein-ordner"
    blocked.write_text("Datei statt Ordner")
    monkeypatch.setattr(persist, "db_path", lambda: blocked / "memory.sqlite3")
    job = jobs.create("Trotzdem arbeiten")
    jobs.finish(job, jobs.COMPLETED)
    assert job["status"] == jobs.COMPLETED
    assert "nicht gespeichert" in capsys.readouterr().out


def test_tabellen_liegen_in_der_gedaechtnis_datenbank():
    from jarvis import config

    jobs.create("x")
    with sqlite3.connect(config.data_dir() / "memory.sqlite3") as c:
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"jobs", "approvals"} <= names
