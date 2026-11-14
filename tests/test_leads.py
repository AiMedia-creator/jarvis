"""Cold-Call-CRM: Leads, Duplikatschutz, Versionen, Ergebniserfassung, Session-Zahlen und CSV-Import."""

import datetime
import json
import time

import pytest

from jarvis import anrufe, leads, persist


@pytest.fixture(autouse=True)
def _eigene_db(tmp_path, monkeypatch):
    monkeypatch.setattr(persist, "db_path", lambda: tmp_path / "memory.sqlite3")
    yield


def _lead(**kw):
    return leads.anlegen({"firma": "Muster GmbH", "name": "Erika Beispiel", "telefon": "089 123456", **kw})


def test_statusmodell_enthaelt_die_fuenf_pflichtstatus():
    titel = {v["titel"] for v in leads.STATUS.values()}
    assert {"Nicht erreicht", "Meeting gebucht", "Kein Interesse", "Mailbox", "Follow-up"} <= titel


def test_telefon_schluessel_vereinheitlicht_schreibweisen():
    assert leads.telefon_schluessel("089 / 12 34 56") == leads.telefon_schluessel("+49 89 123456") == leads.telefon_schluessel("0049 89 123456")
    assert leads.telefon_schluessel("123") == ""


def test_anlegen_ist_idempotent_und_startet_als_neu():
    a = leads.anlegen({"firma": "Alpha AG", "name": "Max"}, request_id="r-1")
    b = leads.anlegen({"firma": "Alpha AG", "name": "Max"}, request_id="r-1")
    assert a["id"] == b["id"] and b["wiederholt"] and a["status"] == "neu" and a["version"] == 1
    assert len(leads.liste()) == 1


def test_gleiche_nummer_wird_immer_abgelehnt_auch_mit_bestaetigung():
    first = _lead()
    with pytest.raises(leads.Duplikat) as exc:
        leads.anlegen({"firma": "Andere Firma", "name": "Jemand", "telefon": "+49 89 123456"}, duplikat_bestaetigt=True)
    assert exc.value.hart and exc.value.vorhanden["id"] == first["id"]


def test_gleiche_firma_und_name_braucht_bestaetigung():
    _lead(telefon="")
    with pytest.raises(leads.Duplikat) as exc:
        leads.anlegen({"firma": "Muster GmbH & Co. KG", "name": "erika  beispiel"})
    assert not exc.value.hart
    second = leads.anlegen({"firma": "Muster GmbH & Co. KG", "name": "erika  beispiel"}, duplikat_bestaetigt=True)
    assert second["status"] == "neu" and len(leads.liste()) == 2


def test_aendern_verlangt_aktuelle_version():
    lead = _lead()
    changed = leads.aendern(lead["id"], {"rolle": "Geschäftsführung"}, lead["version"])
    assert changed["version"] == 2 and changed["rolle"] == "Geschäftsführung"
    with pytest.raises(leads.Konflikt) as exc:
        leads.aendern(lead["id"], {"rolle": "Einkauf"}, lead["version"])
    assert exc.value.current["rolle"] == "Geschäftsführung"


def test_ergebnis_schreibt_genau_einen_anruf_und_keinen_auftrag():
    from jarvis import orders
    lead = _lead()
    before = len(orders.listing()) if hasattr(orders, "listing") else None
    result = leads.ergebnis(lead["id"], "mailbox", lead["version"], request_id="e-1", notiz="AB besprochen")
    again = leads.ergebnis(lead["id"], "mailbox", lead["version"], request_id="e-1", notiz="AB besprochen")
    assert again["wiederholt"] and again["aktivitaet_id"] == result["aktivitaet_id"]
    assert result["lead"]["status"] == "mailbox" and result["lead"]["anrufe"] == 1
    calls = anrufe.anrufe_liste(None, 7, 50)
    assert len(calls) == 1 and calls[0]["ergebnis"] == "nicht_erreicht" and calls[0]["lead_id"] == lead["id"]
    assert calls[0]["auftrag_id"] == ""
    if before is not None:
        assert len(orders.listing()) == before
    assert anrufe.kennzahlen(7)["gespraeche"] == 1


def test_followup_braucht_datum_und_meeting_braucht_termin():
    lead = _lead()
    with pytest.raises(leads.LeadError, match="Datum"):
        leads.ergebnis(lead["id"], "follow_up", lead["version"])
    with pytest.raises(leads.LeadError, match="Uhrzeit"):
        leads.ergebnis(lead["id"], "meeting_gebucht", lead["version"])
    assert leads.detail(lead["id"])["lead"]["status"] == "neu"  # nichts halb gespeichert
    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    done = leads.ergebnis(lead["id"], "follow_up", lead["version"], followup_am=tomorrow)["lead"]
    assert done["followup_am"] == tomorrow and done["naechster_schritt"] == "Follow-up-Anruf"
    booked = leads.ergebnis(lead["id"], "meeting_gebucht", done["version"], termin_beginn=f"{tomorrow}T10:00")["lead"]
    assert booked["termin_ende"] == f"{tomorrow}T10:30" and booked["followup_am"] == ""


def test_ergebnis_mit_alter_version_aendert_nichts():
    lead = _lead()
    leads.ergebnis(lead["id"], "nicht_erreicht", lead["version"])
    with pytest.raises(leads.Konflikt):
        leads.ergebnis(lead["id"], "kein_interesse", lead["version"])
    assert leads.detail(lead["id"])["lead"]["status"] == "nicht_erreicht"
    assert len(anrufe.anrufe_liste(None, 7, 50)) == 1


def test_anrufliste_stellt_faellige_followups_nach_vorn():
    a = leads.anlegen({"firma": "A", "name": "Neu"})
    b = leads.anlegen({"firma": "B", "name": "Fällig"})
    c = leads.anlegen({"firma": "C", "name": "Erledigt"})
    leads.ergebnis(b["id"], "follow_up", b["version"], followup_am=datetime.date.today().isoformat())
    leads.ergebnis(c["id"], "kein_interesse", c["version"])
    order = [x["id"] for x in leads.liste()]
    assert order == [b["id"], a["id"], c["id"]]
    assert [x["id"] for x in leads.liste("offen")] == [b["id"], a["id"]]
    assert leads.uebersicht()["followups_faellig"] == 1


def test_session_zaehlt_nur_gespeicherte_anrufe():
    start = time.time() - 1
    lead = _lead()
    assert leads.session_zahlen(start)["calls"] == 0
    leads.notiz_hinzufuegen(lead["id"], "Nur eine Notiz")
    lead = leads.detail(lead["id"])["lead"]
    leads.ergebnis(lead["id"], "nicht_erreicht", lead["version"])
    numbers = leads.session_zahlen(start)
    assert numbers["calls"] == 1 and numbers["nach_status"]["nicht_erreicht"] == 1


def test_detail_zeigt_verlauf_neueste_zuerst():
    lead = _lead()
    leads.notiz_hinzufuegen(lead["id"], "Erste Notiz")
    lead2 = leads.detail(lead["id"])["lead"]
    leads.ergebnis(lead["id"], "mailbox", lead2["version"])
    acts = leads.detail(lead["id"])["aktivitaeten"]
    assert [a["art"] for a in acts] == ["anruf", "notiz"]


def test_csv_import_prueft_erst_alles_und_ueberspringt_duplikate():
    _lead()
    text = "firma;name;telefon\nNeu AG;Anna;030 111222\nMuster GmbH;Erika Beispiel;\nNeu AG;Anna;030 111222\n"
    report = leads.csv_import(text)
    assert report["neu"] == 1 and report["duplikate_gesamt"] == 2 and report["importiert"] == 0
    assert len(leads.liste()) == 1  # Prüfung schreibt nichts
    done = leads.csv_import(text, ausfuehren=True)
    assert done["importiert"] == 1 and len(leads.liste()) == 2


def test_csv_import_schreibt_bei_zeilenfehler_nichts():
    text = "firma,name,email\nGut AG,Anna,anna@gut.example\n,,kaputt\nSchlecht AG,Ben,keine-mail\n"
    report = leads.csv_import(text, ausfuehren=True)
    assert report["fehler_gesamt"] == 2 and report["importiert"] == 0 and leads.liste() == []
    with pytest.raises(leads.LeadError, match="Unbekannte Spalten"):
        leads.csv_import("firma,geheim\nA,B\n")


def test_termin_vorlage_ohne_google_meldet_ehrlich(monkeypatch):
    from jarvis.tools import google
    monkeypatch.setattr(google, "is_connected", lambda: False)
    lead = _lead()
    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    booked = leads.ergebnis(lead["id"], "meeting_gebucht", lead["version"], termin_beginn=f"{tomorrow}T09:00")["lead"]
    result = leads.termin_vorlage(booked)
    assert result["status"] == "nicht_verbunden" and "nicht im Kalender" in result["hinweis"]


def test_job_klasse_trennt_gespraeche_von_jobs():
    from jarvis import uebersicht
    assert uebersicht.job_klasse({"art": "vordergrund"}) == "gespraech"
    assert uebersicht.job_klasse({"art": "echtzeit"}) == "gespraech"
    assert uebersicht.job_klasse({}) == "gespraech"
    assert uebersicht.job_klasse({"art": "hintergrund"}) == "job"
    assert uebersicht.job_klasse({"art": "faehigkeit"}) == "job"
    assert uebersicht.job_klasse({"art": "etwas_neues"}) == "job"  # unbekannt bleibt sichtbar
    assert json.dumps(leads.uebersicht()["status"])  # serialisierbar


def test_request_id_kann_nicht_fuer_etwas_anderes_wiederverwendet_werden():
    a = leads.anlegen({"firma": "Alpha AG", "name": "Max"}, request_id="r-x")
    b = leads.anlegen({"firma": "Beta AG", "name": "Eva"})
    with pytest.raises(leads.LeadError, match="anderen Lead"):
        leads.anlegen({"firma": "Ganz Anders", "name": "Tom"}, request_id="r-x")
    leads.ergebnis(a["id"], "mailbox", a["version"], request_id="e-x")
    with pytest.raises(leads.LeadError, match="anderen Aktivität"):
        leads.ergebnis(b["id"], "mailbox", b["version"], request_id="e-x")
    with pytest.raises(leads.LeadError, match="anderen Aktivität"):
        leads.ergebnis(a["id"], "kein_interesse", 2, request_id="e-x")
    leads.notiz_hinzufuegen(b["id"], "Notiz eins", request_id="n-x")
    with pytest.raises(leads.LeadError, match="anderen Aktivität"):
        leads.notiz_hinzufuegen(a["id"], "Notiz eins", request_id="n-x")
    assert leads.detail(b["id"])["lead"]["status"] == "neu" and len(anrufe.anrufe_liste(None, 7, 50)) == 1


# --- Regression P2: vollständiger Bestand, stabile Seiten, CSV-Grenzen -------------------------------------------------

def _masse(n, alt_zuerst=True):
    """Legt n Leads direkt an (schnell); der allererste ist der älteste und hat einen eindeutigen Namen."""
    cm, c = leads._db()
    try:
        now = time.time() - 10_000
        c.execute("BEGIN IMMEDIATE")
        for i in range(n):
            data = {"firma": "Uralt Bergwerk AG" if i == 0 else f"Firma {i:05d} GmbH", "name": f"Person {i}", "rolle": "", "telefon": f"030 9{i:06d}",
                    "email": "", "website": "", "ort": "Görlitz" if i == 0 else "Berlin", "quelle": "", "notiz": "", "naechster_schritt": "",
                    "telefon_status": "unklar", "projekt_id": "", "followup_am": "", "termin_beginn": "", "termin_ende": "",
                    "letzte_aktivitaet": None, "letzte_aktivitaet_text": "", "anrufe": 0}
            c.execute("INSERT INTO leads (id, request_id, version, created, updated, status, telefon_key, namens_key, data) VALUES (?,?,?,?,?,?,?,?,?)",
                      (f"L-m{i:06d}", None, 1, now + i, now + i, "neu", leads.telefon_schluessel(data["telefon"]),
                       leads.namens_schluessel(data["firma"], data["name"]), json.dumps(data, ensure_ascii=False)))
        c.execute("COMMIT")
    finally:
        leads._abschluss(cm, c)


def test_mehr_leads_als_die_alte_grenze_sind_vollstaendig_erreichbar():
    _masse(5200)  # alte Grenzen: 5000 in der Abfrage, 500 in der Übersicht
    ueb = leads.uebersicht()
    assert ueb["gesamt"] == ueb["treffer"] == ueb["offen"] == ueb["zahlen"]["neu"] == 5200
    assert len(ueb["leads"]) == leads.SEITE_STANDARD and ueb["offset"] == 0 and ueb["snapshot_neu"]
    assert len(leads.liste()) == 5200
    # Alle Seiten zusammen ergeben jeden Lead genau einmal, in einer festen Reihenfolge
    gesehen, offset = [], 0
    while offset < ueb["treffer"]:
        seite = leads.uebersicht(offset=offset, limit=200, snapshot=ueb["snapshot"])
        assert not seite["snapshot_neu"] and seite["snapshot"] == ueb["snapshot"]
        gesehen += [x["id"] for x in seite["leads"]]
        offset += 200
    assert len(gesehen) == len(set(gesehen)) == 5200 and gesehen == [x["id"] for x in leads.liste()]
    # Letzte Seite und ein Versatz hinter dem Ende
    assert len(leads.uebersicht(offset=5150, limit=50, snapshot=ueb["snapshot"])["leads"]) == 50
    assert leads.uebersicht(offset=99_999, limit=50, snapshot=ueb["snapshot"])["offset"] == 5150


def test_suche_findet_den_aeltesten_eintrag_jenseits_der_alten_grenze():
    _masse(5200)
    for begriff in ("uralt bergwerk", "GÖRLITZ", "030 9000000"):
        treffer = leads.uebersicht(suche=begriff)
        assert treffer["treffer"] == 1 and treffer["leads"][0]["id"] == "L-m000000" and treffer["gesamt"] == 5200
    assert [x["id"] for x in leads.liste(suche="Uralt")] == ["L-m000000"]
    # Text mit Ziffern sucht im Namen, nicht in Telefonnummern („Firma 00012“ trifft nicht jede Nummer mit 00012)
    assert [x["id"] for x in leads.liste(suche="Firma 00012")] == ["L-m000012"]
    assert len(leads.liste(suche="9000012")) == 1 and len(leads.liste(suche="030 90000")) == 100


def test_statusfilter_zahlen_und_faellige_followups_zaehlen_den_ganzen_bestand():
    _masse(5200)
    gestern = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    morgen = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    leads.ergebnis("L-m000000", "follow_up", 1, followup_am=gestern)   # ältester Lead, fällig
    leads.ergebnis("L-m000001", "follow_up", 1, followup_am=morgen)    # noch nicht fällig
    leads.ergebnis("L-m005199", "kein_interesse", 1)
    ueb = leads.uebersicht("follow_up")
    assert ueb["treffer"] == 2 and ueb["zahlen"] == {**{k: 0 for k in leads.STATUS}, "neu": 5197, "follow_up": 2, "kein_interesse": 1}
    assert ueb["followups_faellig"] == 1 and ueb["offen"] == 5199 and ueb["gesamt"] == 5200
    assert leads.uebersicht("offen")["leads"][0]["id"] == "L-m000000"  # fälliges Follow-up steht vorn
    assert leads.uebersicht("kein_interesse")["leads"][0]["id"] == "L-m005199"


def test_reihenfolge_bleibt_beim_blaettern_stabil_auch_wenn_sich_leads_aendern():
    _masse(130)
    erste = leads.uebersicht("offen", limit=50)
    ids_1 = [x["id"] for x in erste["leads"]]
    # Ein Ergebnis auf Seite 1 würde den Lead ohne Schnappschuss ans Ende schieben und Seite 2 um eins verrücken.
    leads.ergebnis(ids_1[0], "nicht_erreicht", 1)
    leads.ergebnis(ids_1[1], "kein_interesse", 1)
    zweite = leads.uebersicht("offen", offset=50, limit=50, snapshot=erste["snapshot"])
    dritte = leads.uebersicht("offen", offset=100, limit=50, snapshot=erste["snapshot"])
    alle = ids_1 + [x["id"] for x in zweite["leads"]] + [x["id"] for x in dritte["leads"]]
    assert len(alle) == len(set(alle)) == 130 and zweite["treffer"] == 130
    # Die Daten selbst sind frisch: Seite 1 zeigt den neuen Status, an derselben Stelle
    nochmal = leads.uebersicht("offen", limit=50, snapshot=erste["snapshot"])
    assert [x["id"] for x in nochmal["leads"]] == ids_1 and nochmal["leads"][0]["status"] == "nicht_erreicht"
    assert nochmal["zahlen"]["kein_interesse"] == 1
    # Anderer Filter oder unbekannte Kennung: neuer Schnappschuss statt falscher Reihenfolge
    assert leads.uebersicht("neu", snapshot=erste["snapshot"])["snapshot_neu"]
    assert leads.uebersicht("offen", snapshot="S-gibtsnicht")["snapshot_neu"]
    frisch = leads.uebersicht("offen")
    assert frisch["treffer"] == 129 and frisch["snapshot"] != erste["snapshot"]


def test_seite_um_einen_lead_herum_fuer_pfeilnavigation():
    _masse(130)
    ueb = leads.uebersicht(limit=50)
    alle = [x["id"] for x in leads.liste()]
    ziel = alle[77]
    seite = leads.uebersicht(limit=50, snapshot=ueb["snapshot"], um=ziel)
    assert seite["offset"] == 50 and seite["um_position"] == 77 and seite["leads"][27]["id"] == ziel
    assert leads.uebersicht(limit=50, snapshot=ueb["snapshot"], um="L-unbekannt")["um_position"] is None
    with pytest.raises(leads.LeadError):
        leads.uebersicht(um="../x")


def _csv(zeilen, fuell=0):
    kopf = "firma,name,telefon,notiz\n"
    return kopf + "".join(f"CSV Firma {i},Kontakt {i},040 7{i:06d},{'x' * fuell}\n" for i in range(zeilen))


def test_csv_am_zeilenlimit_und_darueber():
    genau = _csv(leads.CSV_MAX_ZEILEN)
    report = leads.csv_import(genau.encode("utf-8"))
    assert report["neu"] == report["zeilen"] == 2000 and report["fehler_gesamt"] == 0 and leads.liste() == []
    assert leads.csv_import(genau.encode("utf-8"), ausfuehren=True)["importiert"] == 2000 and len(leads.liste()) == 2000
    with pytest.raises(leads.LeadError, match="Zu viele Zeilen: 2001"):
        leads.csv_import(_csv(leads.CSV_MAX_ZEILEN + 1).encode("utf-8"), ausfuehren=True)
    assert len(leads.liste()) == 2000
    # Leerzeilen zählen nicht als Datenzeilen
    assert leads.csv_import((_csv(3) + "\n\n,,,\n").encode("utf-8"))["zeilen"] == 3


def test_csv_knapp_unter_und_ueber_dem_bytelimit():
    assert leads.CSV_MAX_BYTES == 1_000_000
    zeilen = 1500
    while len(_csv(zeilen + 1, fuell=600).encode("utf-8")) < leads.CSV_MAX_BYTES - 100:
        zeilen += 1
    basis = _csv(zeilen, fuell=600)
    fehlt = leads.CSV_MAX_BYTES - len(basis.encode("utf-8"))
    assert 100 <= fehlt < 1000  # die Randzeile bleibt unter der Notiz-Grenze
    genau = (basis + "Rand AG,Rand,," + "y" * (fehlt - len("Rand AG,Rand,,") - 1) + "\n").encode("utf-8")
    assert len(genau) == leads.CSV_MAX_BYTES
    assert leads.csv_import(genau)["neu"] == zeilen + 1          # genau 1 MB: erlaubt
    assert leads.csv_import(genau[:-2] + b"\n")["neu"] == zeilen + 1  # 1 Byte darunter
    with pytest.raises(leads.LeadError, match="zu groß"):
        leads.csv_import(genau + b"\n")                    # 1 Byte darüber
    with pytest.raises(leads.LeadError, match="zu groß"):
        leads.csv_import((genau + b"\n").decode("utf-8"))
    assert leads.liste() == []


def test_csv_muss_utf8_sein_und_meldet_fehler_und_duplikate_vollstaendig():
    with pytest.raises(leads.LeadError, match="kein gültiges UTF-8"):
        leads.csv_import("firma,name\nMüller AG,Jörg\n".encode("latin-1"), ausfuehren=True)
    assert leads.liste() == []
    mit_bom = "﻿firma;name;telefon\nMüller & Söhne;Jörg Größe;030 5550001\n".encode("utf-8")
    assert leads.csv_import(mit_bom, ausfuehren=True)["importiert"] == 1
    assert leads.liste()[0]["firma"] == "Müller & Söhne" and leads.liste()[0]["name"] == "Jörg Größe"
    # Fehlerbericht: jede fehlerhafte Zeile mit Nummer und Grund; ein Fehler am Ende verhindert das Schreiben aller Zeilen
    text = "firma,name,email,telefon\n" + "".join(f"Gut {i} AG,Anna {i},,\n" for i in range(150)) + "Schlecht AG,Ben,keine-mail,\n,,nur@mail.example,\nZuviel AG,Cem,,,extra\n"
    report = leads.csv_import(text.encode("utf-8"), ausfuehren=True)
    assert report["importiert"] == 0 and report["fehler_gesamt"] == 3 and report["neu"] == 150 and len(leads.liste()) == 1
    assert [(f["zeile"], f["grund"]) for f in report["fehler"]] == [
        (152, "Die E-Mail-Adresse ist ungültig."), (153, "Firma oder Name ist nötig."), (154, "Mehr Felder als Spalten in der Kopfzeile.")]
    # Duplikate: gegen den Bestand (Nummer, Firma+Name) und innerhalb der Datei, je mit Zeile und Grund
    text = ("firma,name,telefon\nNeu AG,Anna,030 111222\nAndere AG,Bob,+49 30 5550001\nMüller & Söhne,Jörg Größe,\n"
            "Neu AG,Anna,030 999\nDritte AG,Cleo,030 111222\n")
    report = leads.csv_import(text.encode("utf-8"), ausfuehren=True)
    assert report["importiert"] == 1 and report["duplikate_gesamt"] == 4
    assert [(d["zeile"], d["grund"]) for d in report["duplikate"]] == [
        (3, "gleiche Telefonnummer vorhanden"), (4, "gleiche Firma und gleicher Name vorhanden"),
        (5, "doppelt in der Datei"), (6, "doppelt in der Datei")]
    with pytest.raises(leads.LeadError, match="doppelt"):
        leads.csv_import("firma,name,firma\nA,B,C\n")


TOKEN = "offline-unit-test-bridge-credential-only"
AUTH = {"Authorization": "Bearer " + TOKEN}


@pytest.mark.asyncio
async def test_bruecke_nimmt_csv_als_rohdaten_bis_1_mb_und_liefert_seiten():
    from aiohttp.test_utils import TestClient, TestServer

    from jarvis import v3_bridge

    client = TestClient(TestServer(v3_bridge.create_app(TOKEN, demo=True)))
    await client.start_server()
    try:
        gross = _csv(1500, fuell=600).encode("utf-8")  # rund 0,95 MB – weit über den alten 16/200 KB
        assert 900_000 < len(gross) < leads.CSV_MAX_BYTES
        r = await client.post("/internal/leads/import", data=gross, headers=AUTH)
        assert r.status == 200 and (await r.json())["neu"] == 1500 and leads.liste() == []
        r = await client.post("/internal/leads/import?ausfuehren=1", data=gross, headers=AUTH)
        assert (await r.json())["importiert"] == 1500
        r = await client.post("/internal/leads/import?ausfuehren=1", data=b"x" * (leads.CSV_MAX_BYTES + 1), headers=AUTH)
        assert r.status == 413 and "zu groß" in (await r.json())["fehler"]
        r = await client.post("/internal/leads/import", data="firma\nM\xfcller\n".encode("latin-1"), headers=AUTH)
        assert r.status == 400 and "UTF-8" in (await r.json())["fehler"]
        r = await client.get("/internal/leads?limit=25&offset=50", headers=AUTH)
        erste = await r.json()
        assert erste["treffer"] == 1500 and len(erste["leads"]) == 25 and erste["offset"] == 50 and erste["csv_max_bytes"] == 1_000_000
        r = await client.get(f"/internal/leads?limit=25&offset=1475&snapshot={erste['snapshot']}", headers=AUTH)
        assert len((await r.json())["leads"]) == 25
        r = await client.get("/internal/leads?limit=abc", headers=AUTH)
        assert r.status == 400
    finally:
        await client.close()
