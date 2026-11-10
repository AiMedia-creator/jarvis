import pytest

from jarvis.tools import ToolFailure, hubspot


@pytest.fixture
def hs(mock_server, monkeypatch):
    monkeypatch.setenv("HUBSPOT_TOKEN", "tok")

    def respond(method, path, body):
        if path.startswith("/crm/v3/objects/contacts/search"):
            return 200, {"results": [{"id": "101", "properties": {"firstname": "Max", "lastname": "Muster",
                                                                   "email": "max@x.de", "company": "Muster GmbH"}}]}
        if path.startswith("/crm/v3/pipelines/deals"):
            return 200, {"results": [{"label": "Vertrieb", "stages": [{"id": "qual", "label": "Qualifiziert"}]}]}
        if path.startswith("/crm/v3/objects/deals/search"):
            return 200, {"results": [{"id": "9", "properties": {"dealname": "Rahmenvertrag", "amount": "12000",
                                                                 "dealstage": "qual", "hs_is_closed": "false"}}]}
        if "/associations/default/" in path:
            return 200, {}
        if path.startswith("/crm/v3/objects/") and method == "POST":
            return 201, {"id": "777"}
        if path == "/forbidden":
            return 403, {"message": "scope missing"}
        return 404, {"message": "nope"}

    url, log = mock_server(respond)
    monkeypatch.setattr(hubspot, "API", url)
    hubspot._stage_cache.clear()
    return log


def test_kontakt_und_deals(hs):
    assert "Max Muster" in hubspot.kontakt_suchen("Max")
    out = hubspot.deals_suchen()
    assert "12.000 EUR" in out and "Qualifiziert" in out
    assert all(e["headers"]["Authorization"] == "Bearer tok" for e in hs)


@pytest.mark.parametrize("fn,kw", [
    (hubspot.notiz_erstellen, {"text": "Telefonat", "kontakt_id": "101"}),
    (hubspot.aufgabe_erstellen, {"titel": "Nachfassen", "faellig": "2026-10-20", "kontakt_id": "101", "deal_id": "9"}),
    (hubspot.mail_protokollieren, {"betreff": "Angebot", "text": "Hallo", "kontakt_id": "101"}),
])
def test_schreiben_nur_mit_freigabe(hs, fn, kw):
    with pytest.raises(ToolFailure):
        fn(bestaetigt_vom_nutzer=False, **kw)
    assert not any(e["method"] == "POST" and "/objects/" in e["path"] and "search" not in e["path"] for e in hs)
    assert "777" in fn(bestaetigt_vom_nutzer=True, **kw)


def test_aufgabe_faelligkeit_und_verknuepfung(hs):
    hubspot.aufgabe_erstellen("X", True, faellig="2026-10-20", kontakt_id="101", deal_id="9")
    task = next(e for e in hs if e["path"] == "/crm/v3/objects/tasks")["body"]
    assert task["properties"]["hs_timestamp"].startswith("2026-10-20T")
    links = [e["path"] for e in hs if "associations/default" in e["path"]]
    assert any(p.endswith("/contacts/101") for p in links) and any(p.endswith("/deals/9") for p in links)


def test_403_meldung(hs):
    with pytest.raises(ToolFailure, match="Scope"):
        hubspot._call("GET", "/forbidden")
