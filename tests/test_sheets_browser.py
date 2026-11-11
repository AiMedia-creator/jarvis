"""Google Sheets (ohne echtes Google-Konto) und Browser-Anbindung (ohne echten Chrome)."""

import json
import types

import pytest

from jarvis import approvals, browser, mcp_client, policy
from jarvis.tools import ToolFailure
from jarvis.tools import sheets


class _Req:
    def __init__(self, result=None, error=None):
        self.result, self.error = result, error

    def execute(self):
        if self.error:
            raise self.error
        return self.result


class FakeSheets:
    def __init__(self):
        self.calls = []

    def spreadsheets(self):
        return self

    def values(self):
        return self

    def get(self, **kw):
        self.calls.append(("get", kw))
        if "fields" in kw:
            return _Req({"properties": {"title": "Umsatz"}, "sheets": [{"properties": {"title": "Q4"}}]})
        return _Req({"range": "Q4!A1:B2", "values": [["Kunde", "Betrag"], ["Weber", "1200"]]})

    def update(self, **kw):
        self.calls.append(("update", kw))
        return _Req({"updatedRange": kw["range"]})

    def append(self, **kw):
        self.calls.append(("append", kw))
        return _Req({"updates": {"updatedRange": "Q4!A3:B3"}})

    def create(self, **kw):
        self.calls.append(("create", kw))
        return _Req({"spreadsheetId": "neu123", "spreadsheetUrl": "https://docs.google.com/spreadsheets/d/neu123"})


SID = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"


@pytest.fixture
def fake(monkeypatch):
    service = FakeSheets()
    monkeypatch.setattr(sheets, "_service", lambda: service)
    return service


def test_id_aus_url_und_ungueltiges():
    assert sheets.spreadsheet_id(f"https://docs.google.com/spreadsheets/d/{SID}/edit#gid=0") == SID
    with pytest.raises(ToolFailure):
        sheets.spreadsheet_id("../../etc/passwd")


def test_lesen_markiert_inhalt_als_information(fake):
    text = sheets.sheet_lesen(SID)
    assert text.startswith("[TABELLEN-INHALT") and "Weber;1200" in text and "Q4" in text
    assert fake.calls[1][1]["range"] == "'Q4'"


def test_schreiben_nur_nach_ja_und_nie_als_formel(fake):
    with pytest.raises(ToolFailure, match="Nicht geschrieben"):
        sheets.sheet_schreiben(SID, "Q4!A3", '[["Weber", 1]]', False)
    assert fake.calls == []
    out = sheets.sheet_schreiben(SID, "Q4!A3", '[["=IMPORTXML(\\"http://x\\")", 5]]', True)
    assert "2 Zellen" in out
    kind, kw = fake.calls[-1]
    assert kind == "update" and kw["valueInputOption"] == "RAW"
    sheets.sheet_schreiben(SID, "Q4!A1", '[["Neu", 2]]', True, anhaengen=True)
    assert fake.calls[-1][0] == "append" and fake.calls[-1][1]["valueInputOption"] == "RAW"
    with pytest.raises(ToolFailure, match="JSON"):
        sheets.sheet_schreiben(SID, "Q4!A1", "kein json", True)
    with pytest.raises(ToolFailure, match="Zu viele"):
        sheets.sheet_schreiben(SID, "Q4!A1", json.dumps([[1] * 50] * 50), True)


def test_fremde_tabelle_ohne_schreibrecht_klare_meldung(monkeypatch):
    from googleapiclient.errors import HttpError

    error = HttpError(types.SimpleNamespace(status=403, reason="insufficient"), b"{}")
    service = FakeSheets()
    service.update = lambda **kw: _Req(error=error)
    monkeypatch.setattr(sheets, "_service", lambda: service)
    monkeypatch.setattr(sheets, "_granted", lambda: {"https://www.googleapis.com/auth/drive.file"})
    with pytest.raises(ToolFailure, match="google-verbinden"):
        sheets.sheet_schreiben(SID, "Q4!A1", '[["x"]]', True)


def test_schreiben_laeuft_ueber_freigabe_lesen_nicht(fake):
    tools = {t.name: t for t in sheets.tools()}
    assert "bestaetigt_vom_nutzer" in tools["sheet_schreiben"].properties
    assert "bestaetigt_vom_nutzer" not in tools["sheet_lesen"].properties
    assert policy.kind("sheet_lesen") == "read"
    assert approvals.ACTIONS["sheet_schreiben"][1] == "Google Sheets"
    preview = approvals.PREVIEWS["sheet_schreiben"]({"tabelle": SID, "bereich": "Q4!A1", "werte": '[["a", 1]]'})
    assert {"feld": "Werte", "wert": "a;1"} in preview


# --- Browser (browser-harness über MCP) ---

def test_browser_aus_bis_eingeschaltet(monkeypatch):
    monkeypatch.delenv("JARVIS_BROWSER", raising=False)
    monkeypatch.setattr(browser.config, "load", lambda: {})
    assert browser.tools() == [] and browser.server_spec() is None


def test_browser_spec_ohne_cloud_und_telemetrie(monkeypatch, tmp_path):
    exe = tmp_path / "browser-harness-mcp"
    exe.write_text("#!/bin/sh\n")
    monkeypatch.setenv("JARVIS_BROWSER", "an")
    monkeypatch.setenv("JARVIS_BROWSER_BEFEHL", str(exe))
    spec = browser.server_spec()
    assert spec["befehl"] == [str(exe)] and spec["env"]["BH_TELEMETRY"] == "0"
    assert "BU_AUTOSPAWN" not in spec["env"]  # browser-harness wertet jeden gesetzten Wert als „an“
    names = {t.name: t for t in browser.tools()}
    for action in ("browser_klicken", "browser_tippen", "browser_feld_fuellen", "browser_taste"):
        assert "bestaetigt_vom_nutzer" in names[action].properties
    for read in ("browser_seite", "browser_bild", "browser_tabs"):
        assert policy.kind(read, {}, names[read]) == "read"
    assert policy.kind("browser_oeffnen", {}, names["browser_oeffnen"]) == "local"
    assert not any(n in names for n in ("browser_js", "browser_cdp", "browser_upload_file", "browser_http_get"))
    # „browser“ aus ~/.jarvis/mcp.json würde die Rohwerkzeuge freigeben – ist reserviert
    monkeypatch.setattr(mcp_client, "config_path", lambda: tmp_path / "mcp.json")
    (tmp_path / "mcp.json").write_text(json.dumps({"server": {"browser": {"befehl": ["x"]}, "andere": {"befehl": ["y"]}}}))
    assert list(mcp_client.load_config()) == ["andere"]


@pytest.fixture
def browser_calls(monkeypatch, tmp_path):
    exe = tmp_path / "browser-harness-mcp"
    exe.write_text("#!/bin/sh\n")
    monkeypatch.setenv("JARVIS_BROWSER", "an")
    monkeypatch.setenv("JARVIS_BROWSER_BEFEHL", str(exe))
    calls = []

    def fake_call(tool, args):
        calls.append((tool, args))
        if tool == "browser_js":
            return {"wert": json.dumps({"url": "https://example.org/", "titel": "Shop", "text": "Warenkorb",
                                        "elemente": [{"nr": 0, "art": "button", "text": "Kaufen", "x": 10, "y": 20}]})}
        return {"ok": True}

    monkeypatch.setattr(browser, "_call", fake_call)
    return calls


def test_browser_lesen_mit_festem_ausdruck(browser_calls):
    text = browser.browser_seite()
    assert "Kaufen | 10,20" in text and text.startswith("[SEITENINHALT")
    tool, args = browser_calls[0]
    assert tool == "browser_js" and args["expression"] == browser.PAGE_EXPRESSION
    assert "password" in browser.PAGE_EXPRESSION  # Passwortfelder werden nie ausgelesen


def test_browser_oeffnet_nur_oeffentliche_ziele(browser_calls, monkeypatch):
    from jarvis.tools import web

    with pytest.raises(ToolFailure):
        browser.browser_oeffnen("http://127.0.0.1:8080/api/config")
    with pytest.raises(ToolFailure):
        browser.browser_oeffnen("file:///etc/passwd")
    assert browser_calls == []
    monkeypatch.setattr(web, "check_url", lambda url: url)
    assert "Geöffnet" in browser.browser_oeffnen("https://example.org/")
    assert browser_calls[0] == ("browser_new_tab", {"url": "https://example.org/"})


def test_browser_aktionen_nur_mit_ja(browser_calls):
    with pytest.raises(ToolFailure, match="Nicht geklickt"):
        browser.browser_klicken(10, 20, "Kaufen-Knopf", False)
    with pytest.raises(ToolFailure, match="Erlaubte Tasten"):
        browser.browser_taste("cmd+q", True)
    assert browser_calls == []
    browser.browser_klicken(10, 20, "Kaufen-Knopf", True)
    assert browser_calls == [("browser_click", {"x": 10, "y": 20})]


def test_nicht_aktivierte_sheets_api_wird_benannt(monkeypatch):
    from googleapiclient.errors import HttpError

    error = HttpError(types.SimpleNamespace(status=403, reason="x"), b'{"error": {"status": "PERMISSION_DENIED", "details": [{"reason": "SERVICE_DISABLED"}]}}')
    service = FakeSheets()
    service.get = lambda **kw: _Req(error=error)
    monkeypatch.setattr(sheets, "_service", lambda: service)
    with pytest.raises(ToolFailure, match="Sheets API"):
        sheets.sheet_lesen(SID)
