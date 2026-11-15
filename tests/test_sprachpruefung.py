"""Sprach-Türsteher: Regeln, Wahrheitstabelle, genaue Anfrage an TypeSafe, Fehler nie als „nein“."""

import json

import httpx
import pytest

from jarvis import sprachpruefung as S


def werte(**kw):
    base = {"verstaendlich": 0.95, "an_jarvis": 0.9, "stopp_wunsch": 0.05, "mehr_als_stopp": 0.1, "nur_rueckmeldung": 0.05}
    base.update(kw)
    return base


@pytest.mark.parametrize("w, spricht, aktion", [
    (werte(), False, S.ANTWORTEN),
    (werte(), True, S.ANTWORTEN),
    (werte(verstaendlich=0.1), False, S.IGNORIEREN),                      # Kauderwelsch aus Hintergrundgeräusch
    (werte(verstaendlich=0.5), False, S.ANTWORTEN),                       # unklar und Jarvis wartet: antworten
    (werte(an_jarvis=0.1), False, S.IGNORIEREN),                          # Fernseher, Gespräch im Raum
    (werte(an_jarvis=0.1), True, S.IGNORIEREN),
    (werte(an_jarvis=0.5), False, S.ANTWORTEN),                           # wartet: im Zweifel antworten
    (werte(an_jarvis=0.5), True, S.IGNORIEREN),                           # spricht: im Zweifel weiterreden
    (werte(stopp_wunsch=0.9, mehr_als_stopp=0.1), True, S.STOPPEN),
    (werte(stopp_wunsch=0.9, mehr_als_stopp=0.5), True, S.STOPPEN),       # nur „ja“ beim Zusatz zählt als mehr
    (werte(stopp_wunsch=0.9, mehr_als_stopp=0.9), True, S.ANTWORTEN),     # „Stopp, nimm lieber Donnerstag“
    (werte(stopp_wunsch=0.9, mehr_als_stopp=0.1), False, S.ANTWORTEN),    # nichts zu stoppen
    (werte(nur_rueckmeldung=0.9), True, S.IGNORIEREN),                    # „mhm“ während er spricht
    (werte(nur_rueckmeldung=0.9), False, S.ANTWORTEN),                    # „Ja“ als Antwort auf seine Frage
])
def test_wahrheitstabelle(w, spricht, aktion):
    assert S.entscheide(w, spricht)[0] == aktion


def test_jede_kombination_hat_ein_ergebnis():
    stufen = [0.1, 0.5, 0.9]
    for v in stufen:
        for a in stufen:
            for s in stufen:
                for m in stufen:
                    for r in stufen:
                        for spricht in (True, False):
                            aktion, grund = S.entscheide(dict(verstaendlich=v, an_jarvis=a, stopp_wunsch=s,
                                                              mehr_als_stopp=m, nur_rueckmeldung=r), spricht)
                            assert aktion in (S.ANTWORTEN, S.STOPPEN, S.IGNORIEREN) and grund


def test_regeln_ohne_modell():
    assert S.regeln("", True) == (S.IGNORIEREN, "leer")
    assert S.regeln("Stopp!", True)[0] == S.STOPPEN
    assert S.regeln("Jarvis, stopp", True)[0] == S.STOPPEN
    assert S.regeln("Stopp", False)[0] == S.IGNORIEREN
    assert S.regeln("Jarvis, wie wird das Wetter?", False)[0] == S.ANTWORTEN
    assert S.regeln("Stopp, nimm lieber Donnerstag", True) is None  # mehr als Stopp: Jev entscheidet
    assert S.regeln("Hey Jarvis, was steht an?", False)[0] == S.ANTWORTEN
    assert S.regeln("Im Film sagt Tony zu Jarvis, er soll starten", True) is None  # Name mitten im Satz: Jev entscheidet


def test_ohne_schluessel_bleibt_alles_wie_bisher(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert not S.aktiv()
    r = S.pruefe("Was steht heute an?", "", False)
    assert r["aktion"] == S.ANTWORTEN and r["quelle"] == "aus"


def test_abschaltbar(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts_test")
    monkeypatch.setenv("JARVIS_TYPESAFE_SPRACHPRUEFUNG", "aus")
    assert not S.aktiv()


def mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_anfrage_enthaelt_nur_aeusserung_und_kontext(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts_test")
    gesendet = {}

    def handler(request):
        gesendet["auth"] = request.headers["authorization"]
        gesendet["body"] = json.loads(request.content)
        assert str(request.url) == S.API_URL
        return httpx.Response(200, json={"model": "jev-1.14.0", "answers": {
            k: {"type": "noul", "noul": v} for k, v in werte(an_jarvis=0.05).items()}})

    r = S.pruefe("Schatz, hast du die Schlüssel gesehen?", "Du hast heute " + "x" * 900, True, client=mock_client(handler))
    assert r == {**r, "aktion": S.IGNORIEREN, "grund": "nicht an Jarvis", "quelle": "typesafe"}
    body = gesendet["body"]
    assert gesendet["auth"] == "Bearer ts_test"
    assert body["model"] == S.MODELL
    assert set(body["state"]) == {"situation", "jarvis_said", "heard"}           # keine weiteren Daten
    assert len(body["state"]["jarvis_said"]) == S.KONTEXT_ZEICHEN               # gekürzt, Ende bleibt
    assert set(body["questions"]) == set(S.FRAGEN)
    assert all(q["type"] == "noul" and "`heard`" in q["instructions"] for q in body["questions"].values())


@pytest.mark.parametrize("antwort", [
    httpx.Response(500, json={"fehler": "x"}),
    httpx.Response(200, json={"answers": {"an_jarvis": {"noul": 0.1}}}),        # unvollständig
    httpx.Response(200, json={"answers": {k: {"noul": 7} for k in S.FRAGEN}}),  # ungültiger Wert
])
def test_fehler_wird_nie_als_nein_gedeutet(monkeypatch, antwort):
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts_test")
    r = S.pruefe("Was steht heute an?", "", True, client=mock_client(lambda request: antwort))
    assert r["aktion"] == S.ANTWORTEN and r["quelle"] == "fehler"


def test_zeitueberschreitung_antwortet(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts_test")

    def handler(request):
        raise httpx.ReadTimeout("zu langsam", request=request)

    r = S.pruefe("Was steht heute an?", "", False, client=mock_client(handler))
    assert r["aktion"] == S.ANTWORTEN and r["grund"] == "typesafe-fehler: ReadTimeout"


def test_alte_oberflaeche_nutzt_den_tuersteher(monkeypatch):
    from jarvis import bargein

    monkeypatch.setenv("TYPESAFE_API_KEY", "ts_test")
    monkeypatch.setattr(S, "pruefe", lambda text, kontext, jarvis_spricht: {
        "aktion": S.IGNORIEREN, "grund": "nicht an Jarvis", "quelle": "typesafe", "werte": {}, "ms": 1})
    r = bargein.should_interrupt("Kannst du mir das Salz geben", "Du hast heute drei Termine")
    assert r == {"unterbrechen": False, "grund": "typesafe:nicht an Jarvis"}


def test_einstellungen_zeigen_status_ohne_schluessel_und_schalten_um(monkeypatch):
    from jarvis import echtzeit

    monkeypatch.setenv("TYPESAFE_API_KEY", "ts_geheim")
    status = echtzeit.status()["tuersteher"]
    assert status == {"an": True, "aktiv": True, "modell": S.MODELL, "problem": None}
    assert "ts_geheim" not in str(echtzeit.status())
    echtzeit.save_settings({"tuersteher": False})
    assert echtzeit.status()["tuersteher"]["aktiv"] is False
    echtzeit.save_settings({"tuersteher": True})
    assert S.aktiv()
    with pytest.raises(ValueError):
        echtzeit.save_settings({"tuersteher": "ja"})


def test_nur_der_letzte_satz_geht_raus():
    vorgelesen = "Du hast drei Termine. Um 9 Uhr Zahnarzt Dr. Weber in der Lindenstraße 4. Soll ich dich erinnern?"
    assert S.letzter_satz(vorgelesen) == "Soll ich dich erinnern?"
    assert S.zustand("Ja", vorgelesen, False)["jarvis_said"] == "Soll ich dich erinnern?"
    assert S.letzter_satz("") == ""


def test_ausgeschaltet_gelten_auch_die_regeln_nicht(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert S.pruefe("Halt", "", False)["aktion"] == S.ANTWORTEN  # ohne Türsteher wie bisher: jede Äußerung zählt
