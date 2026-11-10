"""Sprachausgabe: wiederverwendete ElevenLabs-Verbindung, sofortiges Ausweichen bei ungültigem Schlüssel."""

import types

import pytest

from jarvis import tts


class FakeResponse:
    def __init__(self, status, content=b"mp3"):
        self.status_code, self.content = status, content

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(response=types.SimpleNamespace(status_code=self.status_code))


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setattr(tts, "eleven_problem", None)
    monkeypatch.setattr(tts, "_audio_cache", None)
    monkeypatch.setattr(tts, "_session", None)


def test_verbindung_wird_fuer_alle_saetze_wiederverwendet(monkeypatch):
    sessions, calls = [], []

    class FakeSession:
        def __init__(self):
            sessions.append(self)

        def mount(self, *a):
            pass

        def post(self, url, **kw):
            calls.append(kw["json"]["model_id"])
            return FakeResponse(200)

    monkeypatch.setattr(tts.requests, "Session", FakeSession)
    for text in ("Erster Satz.", "Zweiter Satz.", "Dritter Satz."):
        assert tts.synthesize(text, "eleven:abc") == b"mp3"
    assert len(sessions) == 1 and calls == ["eleven_flash_v2_5"] * 3


def test_ungueltiger_schluessel_faellt_einmal_zurueck_und_fragt_nicht_bei_jedem_satz(monkeypatch):
    posts = []

    class RejectingSession:
        def mount(self, *a):
            pass

        def post(self, url, **kw):
            posts.append(url)
            return FakeResponse(401)

    monkeypatch.setattr(tts.requests, "Session", RejectingSession)
    monkeypatch.setattr(tts, "_edge", lambda text, voice: b"edge:" + text.encode())
    assert tts.synthesize("Eins.", "eleven:abc") == b"edge:Eins."
    assert tts.synthesize("Zwei.", "eleven:abc") == b"edge:Zwei."
    assert len(posts) == 1 and "Schlüssel prüfen" in tts.eleven_problem


@pytest.mark.asyncio
async def test_bridge_wartet_auf_freien_vertonungsplatz_statt_abzulehnen(monkeypatch):
    import asyncio
    import time

    from aiohttp.test_utils import TestClient, TestServer

    from jarvis import v3_bridge

    token = "offline-unit-test-bridge-credential-only"
    monkeypatch.setattr(tts, "synthesize", lambda text, voice=None: (time.sleep(0.3), b"ID3" + text.encode())[1])
    client = TestClient(TestServer(v3_bridge.create_app(token, demo=True)))
    await client.start_server()
    try:
        auth = {"Authorization": "Bearer " + token}
        responses = await asyncio.gather(*[client.post("/internal/tts", json={"text": f"Satz {i}."}, headers=auth)
                                           for i in range(6)])  # doppelt so viele wie Plätze
        assert [r.status for r in responses] == [200] * 6
        assert [await r.read() for r in responses] == [b"ID3" + f"Satz {i}.".encode() for i in range(6)]
    finally:
        await client.close()
