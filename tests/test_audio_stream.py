"""Etappe 5a: deutsche Aussprache-Normalisierung, echtes TTS-Streaming, Abbruch bis zum Anbieter."""

import asyncio
import threading
import time

import pytest
from aiohttp.test_utils import TestClient, TestServer

from jarvis import tts, v3_bridge
from jarvis.speech import for_speech


@pytest.mark.parametrize("raw, spoken", [
    ("Termin am 19.10.2026 um 14:30", "Termin am 19. Oktober 2026 um 14 Uhr 30"),
    ("um 9.30 Uhr und um 16:00", "um 9 Uhr 30 und um 16 Uhr"),
    ("am 2026-10-20", "am 20. Oktober 2026"),
    ("1.500 € bzw. $3,000", "1500 Euro beziehungsweise 3000 Dollar"),
    ("ca. 3,5 Mio. € und 1,99 €", "circa 3,5 Millionen Euro und 1 Euro 99"),
    ("Quote 15 %", "Quote 15 Prozent"),
    ("Tel. +49 170 1234567", "Telefon plus 4 9, 1 7 0, 1 2 3 4 5 6 7"),
    ("z. B. in KW 43", "zum Beispiel in Kalenderwoche 43"),
    ("**Wichtig**: https://example.com/x", "Wichtig: der Link"),
    ("Wir haben 2026 3 Kunden", "Wir haben 2026 3 Kunden"),  # keine Telefonnummer
])
def test_aussprache(raw, spoken):
    assert for_speech(raw) == spoken


def test_eigenes_lexikon_ergaenzt_namen(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    (tmp_path / "aussprache.json").write_text('{"Guliver": "Gulliwer"}', encoding="utf-8")
    assert for_speech("Antwort an Guliver und HubSpot") == "Antwort an Gulliwer und Habspott"


class FakeStreamResponse:
    def __init__(self, parts, status=200):
        self.parts, self.status_code, self.closed = parts, status, False

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(response=self)

    def iter_content(self, chunk_size):
        for p in self.parts:
            if self.closed:
                return
            yield p

    def close(self):
        self.closed = True


@pytest.fixture
def eleven(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setattr(tts, "eleven_problem", None)
    monkeypatch.setattr(tts, "_stream_cache", None)
    state = {"responses": [], "calls": []}

    class Session:
        def mount(self, *a):
            pass

        def post(self, url, **kw):
            state["calls"].append((url, kw))
            return state["responses"].pop(0)

    monkeypatch.setattr(tts, "_session", Session())
    return state


def test_pcm_stuecke_ganze_samples_und_200ms(eleven):
    eleven["responses"].append(FakeStreamResponse([b"\x01" * 5001, b"\x02" * 5001, b"\x03" * 7]))
    mime, parts = tts.stream("Hallo um 14:30.", "eleven:abc")
    chunks = list(parts)
    assert mime == "audio/pcm;rate=22050"
    assert [len(c) for c in chunks] == [tts.CHUNK_BYTES, 10008 - tts.CHUNK_BYTES]
    assert all(len(c) % 2 == 0 for c in chunks)
    url, kw = eleven["calls"][0]
    assert url.endswith("/stream") and kw["params"]["output_format"] == "pcm_22050" and kw["stream"] is True
    assert kw["json"]["text"] == "Hallo um 14 Uhr 30."  # normalisierte Fassung geht an den Anbieter
    cached_mime, cached = tts.stream("Hallo um 14:30.", "eleven:abc")  # zweiter Aufruf aus dem Zwischenspeicher
    assert cached_mime == mime and b"".join(cached) == b"".join(chunks) and len(eleven["calls"]) == 1


def test_abbruch_schliesst_anbieter_verbindung(eleven):
    response = FakeStreamResponse([b"\x00" * 8820] * 50)
    eleven["responses"].append(response)
    cancel = threading.Event()
    mime, parts = tts.stream("Langer Satz.", "eleven:abc", cancel=cancel)
    next(parts)
    cancel.set()
    rest = list(parts)
    assert len(rest) <= 1 and response.closed
    assert tts._stream_cache == {} or ("Langer Satz.", "eleven:abc", tts.speed()) not in (tts._stream_cache or {})


def test_ausfall_vor_erstem_stueck_faellt_auf_kostenlose_stimme(eleven, monkeypatch):
    eleven["responses"].append(FakeStreamResponse([], status=503))
    monkeypatch.setattr(tts, "_edge", lambda text, voice: b"ID3-mp3")
    mime, parts = tts.stream("Hallo.", "eleven:abc")
    assert mime == "audio/mpeg" and list(parts) == [b"ID3-mp3"]


TOKEN = "offline-unit-test-bridge-credential-only"


@pytest.mark.asyncio
async def test_bruecke_streamt_und_bricht_bei_auflegen_ab(monkeypatch):
    produced, cancelled = [], threading.Event()

    def fake_stream(text, voice=None, cancel=None):
        def gen():
            try:
                for i in range(200):
                    if cancel.is_set():
                        return
                    produced.append(i)
                    time.sleep(0.01)
                    yield bytes([i % 256]) * 8820
            finally:  # endet durch Abbruch-Signal ODER durch Schließen des Generators (wie die echte Anbieter-Verbindung)
                if len(produced) < 200:
                    cancelled.set()
        return "audio/pcm;rate=22050", gen()

    monkeypatch.setattr(tts, "stream", fake_stream)
    client = TestClient(TestServer(v3_bridge.create_app(TOKEN, demo=False)))
    await client.start_server()
    try:
        r = await client.post("/internal/tts/stream", json={"text": "Hallo."}, headers={"Authorization": "Bearer " + TOKEN})
        assert r.status == 200 and r.headers["X-Audio-Mime"] == "audio/pcm;rate=22050"
        first = await r.content.readexactly(8820)
        assert first == bytes([0]) * 8820
        r.close()  # Java legt auf (STOP)
        for _ in range(100):
            if cancelled.is_set():
                break
            await asyncio.sleep(0.05)
        assert cancelled.is_set() and len(produced) < 100  # Erzeugung wurde früh beendet, nicht zu Ende gelesen
    finally:
        await client.close()
