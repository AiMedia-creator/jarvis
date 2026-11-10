"""Kommandozentrale: Aufträge (versioniert, idempotent, auditiert), strukturierter Kalender, Brücken-Endpunkte."""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from jarvis import approvals, calendar_data, orders, v3_bridge
from jarvis.tools import Tool, ToolFailure, boolean, string


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    orders._ready.clear()
    return tmp_path


# --- Aufträge -------------------------------------------------------------------------------------------

def test_auftrag_versioniert_mit_konflikt_statt_ueberschreiben():
    o = orders.create({"titel": "Website Steuerberater", "kunde": "Kanzlei Weber", "prioritaet": "hoch"})
    assert o["status"] == "eingang" and o["version"] == 1 and o["jobs"] == [] and o["runs"] == []
    moved = orders.update(o["id"], {"status": "in_arbeit", "position": 500}, expected_version=1)
    assert moved["version"] == 2 and moved["status"] == "in_arbeit"
    with pytest.raises(orders.Conflict) as exc:
        orders.update(o["id"], {"status": "erledigt"}, expected_version=1)  # veralteter Stand
    assert exc.value.current["version"] == 2
    assert orders.listing()["auftraege"][0]["status"] == "in_arbeit"  # nichts überschrieben


def test_auftrag_idempotent_und_auditiert():
    o = orders.create({"titel": "Angebot"}, request_id="req-anlegen-1")
    again = orders.create({"titel": "Angebot"}, request_id="req-anlegen-1")
    assert again["id"] == o["id"] and len(orders.listing()["auftraege"]) == 1
    a = orders.update(o["id"], {"status": "geplant"}, expected_version=1, request_id="req-zug-1")
    b = orders.update(o["id"], {"status": "geplant"}, expected_version=1, request_id="req-zug-1")  # Doppelklick
    assert a["version"] == b["version"] == 2
    assert [h["aktion"] for h in orders.history(o["id"])] == ["verschoben", "angelegt"]


def test_auftrag_eingaben_und_verknuepfung_ohne_gleichsetzen():
    o = orders.create({"titel": "CRM-Pflege"})
    for bad in ({"status": "fertig"}, {"prioritaet": "egal"}, {"frist": "morgen"}, {"geheim": 1},
                {"abhaengigkeiten": [o["id"]]}):
        with pytest.raises(orders.OrderError):
            orders.update(o["id"], bad, expected_version=1)
    with pytest.raises(orders.OrderError):
        orders.create({"titel": ""})
    linked = orders.link(o["id"], job_id="job123", run_id="run-abc")
    assert linked["jobs"] == ["job123"] and linked["runs"] == ["run-abc"] and linked["status"] == "eingang"
    assert orders.listing()["auftraege"][0]["version"] == 2


def test_werkzeuge_fuer_jarvis():
    tools = {t.name: t for t in orders.tools()}
    out = tools["auftrag_anlegen"].handler(titel="Follow-up Guliver", kunde="Guliver", prioritaet="hoch")
    oid = out.split()[1]
    assert oid in tools["auftraege_liste"].handler()
    with pytest.raises(ToolFailure, match="Konflikt"):
        tools["auftrag_aktualisieren"].handler(id=oid, version=9, status="geplant")
    # Angelegt über die Laufzeitfähigkeit: der Auftrag ist mit seinem Job verknüpft (dadurch Version 2).
    assert "v2" in out and orders.listing()["auftraege"][0]["jobs"]
    assert "geplant" in tools["auftrag_aktualisieren"].handler(id=oid, version=2, status="geplant")


# --- Kalender ------------------------------------------------------------------------------------------

CAL = {"id": "primary", "summary": "Omar", "timeZone": "Europe/Berlin", "selected": True}


def ev(eid, start, end, **extra):
    s = {"date": start} if len(start) == 10 else {"dateTime": start}
    e = {"date": end} if len(end) == 10 else {"dateTime": end}
    return {"id": eid, "summary": eid, "start": s, "end": e, "status": "confirmed", "etag": f'"{eid}"', **extra}


def test_normalisierung_ganztag_serie_zeitzone_und_ungueltig():
    allday = calendar_data.normalize(ev("urlaub", "2026-10-26", "2026-10-28"), CAL, "Europe/Berlin")
    assert allday["ganztaegig"] and allday["datum_ende"] == "2026-10-28" and allday["zeitzone"] == "Europe/Berlin"
    dst = calendar_data.normalize(ev("nacht", "2026-10-25T01:30:00+02:00", "2026-10-25T02:30:00+01:00"), CAL, "UTC")
    assert dst["gueltig"] and dst["beginn"].endswith("+02:00") and dst["ende"].endswith("+01:00")  # Zeitumstellung
    series = calendar_data.normalize(ev("weekly", "2026-10-19T14:00:00+02:00", "2026-10-19T15:00:00+02:00",
                                        recurringEventId="serie1", originalStartTime={"dateTime": "2026-10-19T13:00:00+02:00"},
                                        conferenceData={"entryPoints": [{"entryPointType": "video", "uri": "https://meet.google.com/abc"}]}),
                                     CAL, "Europe/Berlin")
    assert series["serie_id"] == "serie1" and series["ausnahme"] and series["meeting_link"] == "https://meet.google.com/abc"
    broken = calendar_data.normalize(ev("kaputt", "2026-10-19T15:00:00+02:00", "2026-10-19T14:00:00+02:00"), CAL, "Europe/Berlin")
    assert broken["gueltig"] is False


def test_konflikte_nur_bei_belegenden_terminen():
    me_declined = {"attendees": [{"email": "o@x.de", "self": True, "responseStatus": "declined"}]}
    items = [calendar_data.normalize(x, CAL, "Europe/Berlin") for x in [
        ev("a", "2026-10-19T10:00:00+02:00", "2026-10-19T11:00:00+02:00"),
        ev("b", "2026-10-19T10:30:00+02:00", "2026-10-19T11:30:00+02:00"),
        ev("frei", "2026-10-19T10:15:00+02:00", "2026-10-19T10:45:00+02:00", transparency="transparent"),
        ev("abgesagt", "2026-10-19T10:15:00+02:00", "2026-10-19T10:45:00+02:00", **me_declined),
        ev("ganztag", "2026-10-19", "2026-10-20"),
        ev("danach", "2026-10-19T11:30:00+02:00", "2026-10-19T12:00:00+02:00")]]
    found = calendar_data.conflicts(items)
    assert [(k["a"], k["b"]) for k in found] == [("a", "b")] and found[0]["ab"].startswith("2026-10-19T10:30")


class FakeCal:
    def __init__(self):
        self.calls = []

    def calendarList(self):
        return self

    def events(self):
        return self

    def list(self, **kw):
        self.calls.append(kw)
        outer = self

        class R:
            def execute(self):
                if "calendarId" not in kw:
                    return {"items": [CAL, {"id": "arbeit", "summary": "Arbeit", "selected": True},
                                      {"id": "abo", "summary": "Feiertage", "selected": False}]}
                if kw["calendarId"] == "primary" and not kw.get("pageToken"):
                    return {"items": [ev("p1", "2026-10-19T09:00:00+02:00", "2026-10-19T10:00:00+02:00")],
                            "nextPageToken": "seite2"}
                if kw["calendarId"] == "primary":
                    return {"items": [ev("p2", "2026-10-20T09:00:00+02:00", "2026-10-20T10:00:00+02:00")]}
                return {"items": [ev("w1", "2026-10-19T09:30:00+02:00", "2026-10-19T10:30:00+02:00")]}
        return R()


def test_paginierung_mehrere_kalender_und_textform():
    fake = FakeCal()
    data = calendar_data.events("2026-10-19T00:00:00", "2026-10-21T00:00:00", service=fake)
    assert [e["id"] for e in data["termine"]] == ["p1", "w1", "p2"]
    assert {e["kalender_id"] for e in data["termine"]} == {"primary", "arbeit"}  # nicht ausgewählter fehlt
    assert all(c.get("timeZone") == data["zeitzone"] for c in fake.calls if "calendarId" in c)
    assert len(data["konflikte"]) == 1
    text = calendar_data.as_text(data)
    assert "[p1]" in text and "KONFLIKT" in text and data["zeitzone"] in text
    with pytest.raises(ValueError):
        calendar_data.events("2026-10-21T00:00:00", "2026-10-19T00:00:00", service=fake)


# --- Brücken-Endpunkte ---------------------------------------------------------------------------------

TOKEN = "offline-unit-test-bridge-credential-only"
AUTH = {"Authorization": "Bearer " + TOKEN}


@pytest.mark.asyncio
async def test_bruecke_auftraege_konflikt_und_freigaben():
    client = TestClient(TestServer(v3_bridge.create_app(TOKEN, demo=False)))
    await client.start_server()
    try:
        r = await client.post("/internal/orders", json={"felder": {"titel": "Neuer Lead"}, "request_id": "req-bruecke-1"}, headers=AUTH)
        order = (await r.json())["auftrag"]
        r = await client.patch(f"/internal/orders/{order['id']}", json={"version": 1, "aenderungen": {"status": "geplant"}},
                               headers=AUTH)
        assert r.status == 200
        r = await client.patch(f"/internal/orders/{order['id']}", json={"version": 1, "aenderungen": {"status": "erledigt"}},
                               headers=AUTH)
        assert r.status == 409 and (await r.json())["aktuell"]["version"] == 2
        assert (await client.get("/internal/orders")).status == 401  # ohne Token

        sent = []
        tool = approvals.wrap(Tool("cc_senden", "x", {"an": string("an"), "bestaetigt_vom_nutzer": boolean("ja")},
                                   lambda an, bestaetigt_vom_nutzer: sent.append(an) or "gesendet",
                                   ["an", "bestaetigt_vom_nutzer"]))
        tool.handler(an="k@x.de", bestaetigt_vom_nutzer=False)
        items = (await (await client.get("/internal/approvals", headers=AUTH)).json())["freigaben"]
        item = next(i for i in items if i["werkzeug"] == "cc_senden")
        assert item["laeuft_ab"] > item["erstellt"] and item["vorschau"]["zeilen"]
        r = await client.post(f"/internal/approvals/{item['id']}/freigeben", headers=AUTH)
        assert r.status == 200 and sent == ["k@x.de"]
        r = await client.post(f"/internal/approvals/{item['id']}/freigeben", headers=AUTH)  # doppelt
        assert r.status == 409 and sent == ["k@x.de"]
        assert (await client.post("/internal/approvals/x/loeschen", headers=AUTH)).status == 400
    finally:
        await client.close()
