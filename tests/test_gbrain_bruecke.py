"""Gedächtnis-Sicherung, Export, Rückweg und gbrain-Schutzregeln – immer auf einer eigenen Test-Datenbank."""

import json
import sqlite3

import pytest

from jarvis import config, gbrain_bruecke as g
from jarvis.memory import MemoryStore


@pytest.fixture
def daten(tmp_path, monkeypatch):
    """Repräsentativer Bestand: bestätigt, korrigiert (mit Fassung), Vorschlag, fremder Scope, Gespräch, Notizdatei."""
    home = tmp_path / "jarvis"
    (home / "memories").mkdir(parents=True)
    (home / "memories" / "notiz.md").write_text("Alte Notiz", encoding="utf-8")
    (home / "profil.md").write_text("# Profil", encoding="utf-8")
    monkeypatch.setattr(config, "data_dir", lambda: home)
    store = MemoryStore(home / "memory.sqlite3")
    a = store.remember("Der Kunde Weber bevorzugt Termine am Vormittag.", "kunde", "user")
    b = store.remember("Mein Stundensatz beträgt 80 Euro.", "arbeit", "user")
    store.update_fact(b["id"], "Mein Stundensatz beträgt 95 Euro.", source="user")
    v = store.remember("Vielleicht mag Weber Kaffee.", "kunde", "modell", consent="vorschlag")
    MemoryStore(home / "memory.sqlite3", owner_scope="anderer").remember("Fremder Bereich: geheim halten.", "note", "user")
    store.record_turn("haupt", "Hallo Jarvis", "Hallo Master")
    return {"home": home, "db": home / "memory.sqlite3", "store": store, "a": a["id"], "b": b["id"], "vorschlag": v["id"]}


def _summe(path):
    return g._sha256(path)


def test_sicherung_ist_geprueft_und_laesst_die_quelle_unveraendert(daten):
    before = sqlite3.connect(daten["db"]).execute("SELECT COUNT(*) FROM facts").fetchone()[0]
    manifest = g.sichern()
    assert manifest["pruefung"]["ok"] and manifest["pruefung"]["integrity_check"] == "ok"
    assert manifest["zeilen"]["facts"] == before >= 4 and manifest["zeilen"]["episodes"] == 1  # inkl. übernommener Alt-Notiz
    assert {"memory.sqlite3", "profil.md", "memories/notiz.md"} <= set(manifest["dateien"])
    assert g.sicherung_pruefen(manifest["ordner"])["ok"]
    assert sqlite3.connect(daten["db"]).execute("SELECT COUNT(*) FROM facts").fetchone()[0] == before


def test_beschaedigte_sicherung_faellt_auf_und_wiederherstellen_ueberschreibt_nie(daten, tmp_path):
    manifest = g.sichern()
    ordner = manifest["ordner"]
    ziel = tmp_path / "wieder.sqlite3"
    g.wiederherstellen(ordner, ziel)
    assert _summe(ziel) == manifest["dateien"]["memory.sqlite3"]["sha256"]
    with pytest.raises(g.BrueckeFehler, match="existiert schon"):
        g.wiederherstellen(ordner, ziel)
    with pytest.raises(g.BrueckeFehler, match="existiert schon"):
        g.wiederherstellen(ordner, daten["db"])  # die echte Datenbank wird nie überschrieben
    with open(f"{ordner}/profil.md", "a", encoding="utf-8") as f:
        f.write("manipuliert")
    assert not g.sicherung_pruefen(ordner)["ok"]


def test_export_nimmt_nur_bestaetigte_erinnerungen_des_eigenen_scopes(daten):
    bericht = g.exportieren()
    assert sorted(bericht["ids"]) == sorted([daten["a"], daten["b"]])
    assert bericht["uebersprungen"]["vorschlag"] == 1 and bericht["archiv_tage"] == 0
    text = "".join(p.read_text(encoding="utf-8") for p in (daten["home"] / "gbrain-export").rglob("*.md"))
    assert "95 Euro" in text and "80 Euro" in text  # aktuelle und frühere Fassung
    assert "Kaffee" not in text and "Fremder Bereich" not in text and "Hallo Jarvis" not in text
    seite = (daten["home"] / "gbrain-export" / "jarvis" / "erinnerungen").glob("*.md").__next__().read_text(encoding="utf-8")
    meta, _ = g._lies_seite(seite)
    assert meta["jarvis_id"] and meta["jarvis_created_at"] and meta["jarvis_owner_scope"] == "local" and meta["jarvis_consent"] == "explicit"


def test_archiv_geht_nur_auf_wunsch_hinaus(daten):
    bericht = g.exportieren(mit_archiv=True)
    assert bericht["archiv_tage"] == 1
    assert any("Hallo Jarvis" in p.read_text(encoding="utf-8") for p in (daten["home"] / "gbrain-export" / "jarvis" / "gespraeche").glob("*.md"))


def test_rueckweg_erhaelt_ids_zeitstempel_bestaetigung_scope_fassungen_und_suche(daten, tmp_path):
    bericht = g.exportieren()
    neu = tmp_path / "rueck.sqlite3"
    g.zurueckspielen(bericht["ordner"], neu)
    ergebnis = g.vergleichen(daten["db"], neu)
    assert ergebnis == {"gleich": True, "erinnerungen": 2, "unterschiede": []}
    with pytest.raises(g.BrueckeFehler, match="existiert schon"):
        g.zurueckspielen(bericht["ordner"], neu)
    # Der zurückgespielte Bestand ist über die normale Suche auffindbar (FTS neu aufgebaut)
    treffer = MemoryStore(neu).search("Stundensatz", strict=True)
    assert any("95 Euro" in f["content"] for f in treffer["facts"])
    # Vergleich bemerkt eine Abweichung
    c = sqlite3.connect(neu)
    c.execute("UPDATE facts SET source='geändert' WHERE id=?", (daten["a"],))
    c.commit()
    c.close()
    assert g.vergleichen(daten["db"], neu)["unterschiede"] == [f"{daten['a']}: source weicht ab"]


def test_gbrain_ist_aus_und_bietet_ohne_schalter_kein_werkzeug(daten, monkeypatch):
    monkeypatch.delenv("JARVIS_GBRAIN", raising=False)
    assert g.enabled() is False and g.tools() == []
    status = g.status()
    assert status["fuehrend"].startswith("SQLite") and status["eingeschaltet"] is False


def test_import_wird_verweigert_wenn_embeddings_an_und_cloud_schalter_aus(daten, monkeypatch):
    g.exportieren()
    cfg = g.heim() / ".gbrain"
    cfg.mkdir(parents=True)
    (cfg / "config.json").write_text(json.dumps({"embedding_model": "voyage:voyage-3"}), encoding="utf-8")
    monkeypatch.setenv("JARVIS_ERINNERUNGEN_AN_CLOUD", "aus")
    called = []
    monkeypatch.setattr(g, "_gbrain", lambda *a, **k: called.append(a) or "")
    with pytest.raises(g.BrueckeFehler, match="Import verweigert"):
        g.gbrain_importieren()
    assert called == []
    monkeypatch.setenv("JARVIS_ERINNERUNGEN_AN_CLOUD", "an")
    g.gbrain_importieren()
    assert called and called[-1][0] == "import" and "--no-embed" in called[-1]


def test_gbrain_bekommt_keine_schluessel_und_ein_eigenes_verzeichnis(daten, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("HUBSPOT_TOKEN", "t")
    env = g._umgebung()
    assert "ANTHROPIC_API_KEY" not in env and "HUBSPOT_TOKEN" not in env
    assert env["GBRAIN_HOME"] == str(daten["home"] / "gbrain")


def test_export_leert_nie_ein_fremdes_verzeichnis_und_laesst_geheime_fassungen_zu_hause(daten, tmp_path):
    fremd = tmp_path / "meine-notizen"
    (fremd / "jarvis" / "erinnerungen").mkdir(parents=True)
    (fremd / "jarvis" / "erinnerungen" / "wichtig.md").write_text("nicht löschen", encoding="utf-8")
    with pytest.raises(g.BrueckeFehler, match="kein Jarvis-Export"):
        g.exportieren(fremd)
    assert (fremd / "jarvis" / "erinnerungen" / "wichtig.md").read_text(encoding="utf-8") == "nicht löschen"
    # Eine frühere Fassung mit Zugangsdaten (direkt in die DB gelegt) darf den Rechner nicht verlassen
    c = sqlite3.connect(daten["db"])
    c.execute("INSERT INTO fact_versions (id, fact_id, content, category, source, changed_at, reason, owner_scope, installation_scope) "
              "VALUES ('v-geheim', ?, 'Mein Passwort: hunter2-geheim', 'arbeit', 'user', '2026-01-01T00:00:00+00:00', 'test', 'local', 'default')", (daten["b"],))
    c.commit(); c.close()
    bericht = g.exportieren()
    text = "".join(p.read_text(encoding="utf-8") for p in (daten["home"] / "gbrain-export").rglob("*.md"))
    assert "hunter2" not in text and bericht["uebersprungen"]["geheimnis"] >= 1
    assert g.exportieren()["erinnerungen"] == 2  # der eigene Export lässt sich wiederholen


# --- Regression P2: unvollständige, aber formal gültige Kopien müssen auffallen ----------------------------------------

def _loesche_in_kopie(pfad, sql="DELETE FROM episodes"):
    c = sqlite3.connect(pfad)
    c.execute(sql)
    c.commit()
    c.execute("VACUUM")
    c.close()


def test_unvollstaendige_kopie_faellt_schon_beim_sichern_auf(daten, monkeypatch):
    """Die Kopie ist gültiges SQLite (integrity_check ok), enthält aber weniger Zeilen als die Quelle."""
    monkeypatch.setattr(g, "_kopie_schneiden", _loesche_in_kopie)
    with pytest.raises(g.BrueckeFehler, match="episodes: Kopie 0 gegen erwartet 1") as fehler:
        g.sichern()
    kaputt = [p for p in (daten["home"] / "backups").iterdir()]
    assert len(kaputt) == 1 and kaputt[0].name.endswith("-FEHLERHAFT") and "FEHLERHAFT" in str(fehler.value)
    assert sqlite3.connect(kaputt[0] / "memory.sqlite3").execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_leere_gueltige_sqlite_datei_besteht_die_pruefung_nicht(daten, monkeypatch):
    def leeren(pfad):
        pfad.unlink()
        sqlite3.connect(pfad).close()
    monkeypatch.setattr(g, "_kopie_schneiden", leeren)
    with pytest.raises(g.BrueckeFehler, match="facts: Kopie fehlt"):
        g.sichern()


def test_manifest_haelt_zeilenzahlen_fest_und_spaetere_pruefung_vergleicht_erneut(daten):
    manifest = g.sichern()
    ordner = manifest["ordner"]
    assert manifest["zeilen"]["facts"] >= 4 and manifest["zeilen"]["fact_versions"] >= 1 and manifest["zeilen"]["episodes"] == 1
    assert set(manifest["feldsummen"]) >= {"facts", "fact_versions", "episodes"}
    assert json.loads(open(f"{ordner}/MANIFEST.json", encoding="utf-8").read())["zeilen"] == manifest["zeilen"]
    # Später gekürzt UND die Datei-Prüfsumme im Manifest passend nachgezogen: nur die Zeilenzahlen verraten es noch.
    _loesche_in_kopie(f"{ordner}/memory.sqlite3", "DELETE FROM fact_versions")
    m = json.loads(open(f"{ordner}/MANIFEST.json", encoding="utf-8").read())
    m["dateien"]["memory.sqlite3"]["sha256"] = g._sha256(g.Path(ordner) / "memory.sqlite3")
    open(f"{ordner}/MANIFEST.json", "w", encoding="utf-8").write(json.dumps(m))
    ergebnis = g.sicherung_pruefen(ordner)
    assert not ergebnis["ok"] and any(f.startswith("fact_versions: Kopie 0 gegen erwartet") for f in ergebnis["fehler"])
    with pytest.raises(g.BrueckeFehler, match="beschädigt"):
        g.wiederherstellen(ordner, daten["home"] / "nie.sqlite3")
    assert not (daten["home"] / "nie.sqlite3").exists()


def test_gleiche_zeilenzahl_mit_veraendertem_inhalt_faellt_auf(daten):
    manifest = g.sichern()
    ordner = manifest["ordner"]
    c = sqlite3.connect(f"{ordner}/memory.sqlite3")
    c.execute("UPDATE facts SET source='manipuliert' WHERE id=?", (daten["a"],))
    c.commit(); c.close()
    m = json.loads(open(f"{ordner}/MANIFEST.json", encoding="utf-8").read())
    m["dateien"]["memory.sqlite3"]["sha256"] = g._sha256(g.Path(ordner) / "memory.sqlite3")
    open(f"{ordner}/MANIFEST.json", "w", encoding="utf-8").write(json.dumps(m))
    assert "facts: Feldinhalte weichen vom erwarteten Stand ab" in g.sicherung_pruefen(ordner)["fehler"]


def test_manifest_ohne_zeilenzahlen_gilt_nicht_als_geprueft(daten):
    ordner = g.sichern()["ordner"]
    m = json.loads(open(f"{ordner}/MANIFEST.json", encoding="utf-8").read())
    m.pop("zeilen")
    open(f"{ordner}/MANIFEST.json", "w", encoding="utf-8").write(json.dumps(m))
    ergebnis = g.sicherung_pruefen(ordner)
    assert not ergebnis["ok"] and any("Zeilenzahlen fehlen" in f for f in ergebnis["fehler"])


def test_zaehlung_und_kopie_stammen_aus_demselben_snapshot(daten, monkeypatch):
    """Schreibt Jarvis während der Sicherung weiter, bleibt die Sicherung in sich stimmig (kein Fehlalarm, kein Loch)."""
    echt = g._bestand

    def bestand_und_dann_schreiben(c):
        out = echt(c)
        daten["store"].remember("Während der Sicherung gespeichert: Angebot Müller bis Freitag.", "note", "user")
        return out
    monkeypatch.setattr(g, "_bestand", bestand_und_dann_schreiben)
    vorher = sqlite3.connect(daten["db"]).execute("SELECT COUNT(*) FROM facts").fetchone()[0]
    manifest = g.sichern()
    kopie = sqlite3.connect(f"{manifest['ordner']}/memory.sqlite3").execute("SELECT COUNT(*) FROM facts").fetchone()[0]
    assert manifest["zeilen"]["facts"] == kopie == vorher
    assert sqlite3.connect(daten["db"]).execute("SELECT COUNT(*) FROM facts").fetchone()[0] == vorher + 1
    assert g.sicherung_pruefen(manifest["ordner"])["ok"]


def test_wiederherstellung_wird_nur_in_temporaerer_kopie_geprobt_und_feldweise_verglichen(daten, monkeypatch):
    summe_vorher, zeit_vorher = _summe(daten["db"]), daten["db"].stat().st_mtime_ns
    ziele = []
    echt = g.wiederherstellen
    monkeypatch.setattr(g, "wiederherstellen", lambda ordner, ziel: ziele.append(g.Path(ziel)) or echt(ordner, ziel))
    manifest = g.sichern()
    probe = manifest["wiederherstellung_geprueft"]
    assert probe["ok"] and {"facts", "fact_versions", "episodes"} <= set(probe["verglichen"]) and probe["zeilen"] == manifest["zeilen"]
    assert len(ziele) == 1 and daten["home"] not in ziele[0].parents and not ziele[0].exists()  # temporär und aufgeräumt
    # Die wiederhergestellte Kopie entspricht Feld für Feld dem Ausgangsbestand
    neu = daten["home"].parent / "probe.sqlite3"
    echt(manifest["ordner"], neu)
    assert g.vergleichen(daten["db"], neu) == {"gleich": True, "erinnerungen": 2, "unterschiede": []}
    a, b = sqlite3.connect(daten["db"]), sqlite3.connect(neu)
    for tabelle in ("facts", "fact_versions", "episodes", "memory_events"):
        assert a.execute(f"SELECT * FROM {tabelle} ORDER BY rowid").fetchall() == b.execute(f"SELECT * FROM {tabelle} ORDER BY rowid").fetchall()
    a.close(); b.close()
    # Die führende Datenbank ist unverändert (gleicher Inhalt, nie beschrieben)
    assert _summe(daten["db"]) == summe_vorher and daten["db"].stat().st_mtime_ns == zeit_vorher
    assert g.wiederherstellung_pruefen(manifest["ordner"])["ok"]
