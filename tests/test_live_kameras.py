"""Live-Kameras: Ortserkennung, Live-Pruefung, Fallbacks, UI-Ereignis (offline, Netz gemockt)."""

import pytest

from jarvis import runtime
from jarvis.tools import ToolFailure
from jarvis.tools import live_kameras as K


@pytest.fixture(autouse=True)
def _frisch(monkeypatch):
    K._live_cache.clear()
    monkeypatch.delenv("JARVIS_YOUTUBE_API_KEY", raising=False)
    monkeypatch.delenv("JARVIS_WINDY_API_KEY", raising=False)


class R:
    def __init__(self, text="", ok=True, data=None):
        self.text, self.ok, self._data, self.status_code = text, ok, data, 200 if ok else 404

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def _live_page(title):
    return f'<meta name="title" content="{title}"> "isLiveNow":true "playableInEmbed":true'


@pytest.mark.parametrize("ort,stadt", [("Berlin", "Berlin"), ("tel aviv", "Tel Aviv"), ("Tel-Aviv", "Tel Aviv"),
                                       ("was ist gerade in Tokyo los", "Tokio"), ("New York", "New York"),
                                       ("Kiew", "Kiew"), ("ISS", "Erde aus dem All"), ("Strand von Tel Aviv", "Tel Aviv")])
def test_ortserkennung(ort, stadt):
    assert K.find_entry(ort)["stadt"] == stadt


def test_zeigt_erste_live_quelle_und_sendet_ereignis(monkeypatch):
    pages = {"tw9PIVnDbbA": "beendet", "61b3Jty73Zo": _live_page("Olympiastadion live")}
    monkeypatch.setattr(K.requests, "get", lambda url, **kw: R(pages.get(url.split("v=")[-1], "")))
    before = runtime.bus.since(0, 0)[1]
    out = K.live_kamera("Berlin")
    assert "Olympiastadion" in out and "live" in out
    events, _ = runtime.bus.since(before, 0)
    cam = [e for e in events if e.get("typ") == "kamera"][-1]["kamera"]
    assert cam["video"] == "61b3Jty73Zo" and cam["art"] == "youtube" and cam["stadt"] == "Berlin"


def test_tel_aviv_sagt_ehrlich_dass_es_mehrfachkamera_ist(monkeypatch):
    monkeypatch.setattr(K.requests, "get", lambda url, **kw: R(_live_page("Israel cams")))
    assert "keine eigene" in K.live_kamera("Tel Aviv").lower() or "nicht live" in K.live_kamera("Tel Aviv")


def test_unbekannte_stadt_ueber_suche(monkeypatch):
    def get(url, **kw):
        if "results" in url:
            return R('"videoId":"AAAAAAAAAAA" "videoId":"BBBBBBBBBBB"')
        vid = url.split("v=")[-1]
        return R(_live_page("Hamburg Hafen Live Cam") if vid == "BBBBBBBBBBB" else _live_page("Irgendwas anderes"))
    monkeypatch.setattr(K.requests, "get", get)
    out = K.live_kamera("Hamburg")
    assert "Hamburg Hafen" in out and "Suche" in out


def test_nichts_live_ehrliche_meldung(monkeypatch):
    monkeypatch.setattr(K.requests, "get", lambda url, **kw: R("nichts"))
    with pytest.raises(ToolFailure, match="keine öffentliche Live-Kamera"):
        K.live_kamera("Berlin")


def test_suche_abschaltbar(monkeypatch):
    monkeypatch.setenv("JARVIS_KAMERA_SUCHE", "aus")
    assert K._search_youtube("Hamburg") == []


def test_kamera_schliessen_sendet_ereignis():
    before = runtime.bus.since(0, 0)[1]
    K.kamera_schliessen()
    events, _ = runtime.bus.since(before, 0)
    assert any(e.get("typ") == "kamera" and e.get("aktion") == "schliessen" for e in events)


def test_im_gespraech_verfuegbar():
    from tests.helpers import make_agent

    names = {d["name"] for d in make_agent(channel="web")._request()["tools"]}
    assert {"live_kamera", "kamera_schliessen"} <= names


def _windy_get(webcams, status=200):
    def get(url, **kw):
        if "geocoding" in url:
            return R(data={"results": [{"name": "Tel Aviv", "country": "Israel", "latitude": 32.08, "longitude": 34.78}]})
        if "api.windy.com" in url:
            assert kw["headers"]["x-windy-api-key"] == "testschluessel"
            r = R(data={"webcams": webcams})
            r.status_code = status
            return r
        return R(_live_page("Israel cams"))  # YouTube-Ersatz
    return get


def test_windy_echte_tel_aviv_kamera_vor_ersatz(monkeypatch):
    monkeypatch.setenv("JARVIS_WINDY_API_KEY", "testschluessel")
    monkeypatch.setattr(K.requests, "get", _windy_get([
        {"title": "Tel Aviv: Gordon Beach", "status": "active", "location": {"city": "Tel Aviv"},
         "player": {"day": "https://webcams.windy.com/webcams/public/embed/player/1/day"},
         "urls": {"detail": "https://www.windy.com/webcams/1"}, "lastUpdatedOn": "2026-11-08T19:50:00.000Z"},
        {"title": "Tel Aviv: Hilton Beach", "status": "active", "location": {"city": "Tel Aviv"},
         "player": {"live": "https://webcams.windy.com/webcams/public/embed/player/2/live"}}]))
    before = runtime.bus.since(0, 0)[1]
    out = K.live_kamera("Tel Aviv")
    cam = [e for e in runtime.bus.since(before, 0)[0] if e.get("typ") == "kamera"][-1]["kamera"]
    assert cam["art"] == "windy" and cam["live"] and "Hilton" in cam["titel"] and "Windy.com" in cam["quelle"]
    assert "Hilton" in out and "live" in out


def test_windy_standbild_wenn_kein_player(monkeypatch):
    monkeypatch.setenv("JARVIS_WINDY_API_KEY", "testschluessel")
    monkeypatch.setattr(K.requests, "get", _windy_get([
        {"title": "Strand", "images": {"current": {"preview": "https://imgproxy.windy.com/x.jpg"}}}]))
    cam = K._windy("Tel Aviv")
    assert cam["art"] == "bild" and not cam["live"] and "Standbild" in cam["quelle"]


def test_windy_ungueltiger_schluessel_faellt_auf_youtube_zurueck(monkeypatch):
    monkeypatch.setenv("JARVIS_WINDY_API_KEY", "testschluessel")
    monkeypatch.setattr(K.requests, "get", _windy_get([], status=403))
    out = K.live_kamera("Tel Aviv")
    assert "Israel" in out and "Windy-Schlüssel ungültig" in out


def test_windy_ohne_schluessel_inaktiv():
    assert K._windy("Berlin") is None


def test_windy_lehnt_unsichere_urls_ab(monkeypatch):
    monkeypatch.setenv("JARVIS_WINDY_API_KEY", "testschluessel")
    monkeypatch.setattr(K.requests, "get", _windy_get([
        {"title": "x", "player": {"live": "http://evil.example/p"}, "images": {"current": {"preview": "https://u:p@evil.example/i"}}}]))
    assert K._windy("Tel Aviv") is None


def test_windy_kamera_in_der_stadt_vor_live_kamera_ausserhalb(monkeypatch):
    monkeypatch.setenv("JARVIS_WINDY_API_KEY", "testschluessel")
    monkeypatch.setattr(K.requests, "get", _windy_get([
        {"title": "Herzlia: Marina", "location": {"city": "Herzlia", "latitude": 32.16, "longitude": 34.79},
         "player": {"live": "https://webcams.windy.com/webcams/public/embed/player/9/live"}},
        {"title": "Tel-Aviv: Jaffa Port", "location": {"city": "Tel-Aviv", "latitude": 32.05, "longitude": 34.75},
         "player": {"day": "https://webcams.windy.com/webcams/public/embed/player/3/day"}},
        {"title": "Tel-Aviv: Hilton Beach", "location": {"city": "Tel-Aviv", "latitude": 32.09, "longitude": 34.77},
         "player": {"day": "https://webcams.windy.com/webcams/public/embed/player/1/day"}}]))
    cam = K._windy("Tel Aviv")
    assert cam["titel"] == "Tel-Aviv: Hilton Beach" and cam["in_stadt"] and not cam["live"]
    out = K.live_kamera("Tel Aviv")
    assert "Alternative live" in out and "live-raster" in out
    assert "Israel" in K.live_kamera("Tel Aviv live-raster")
