import asyncio
import json
import uuid

import pytest
from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer

from jarvis import config, memory, v3_bridge

TOKEN = 'offline-unit-test-bridge-credential-only'


@pytest.mark.asyncio
async def test_bridge_memory_and_generation_are_connected(tmp_path, monkeypatch):
    monkeypatch.setenv('JARVIS_DATA_DIR', str(tmp_path))
    client = TestClient(TestServer(v3_bridge.create_app(TOKEN, demo=True)))
    await client.start_server()
    auth = {'Authorization': 'Bearer ' + TOKEN}
    try:
        response = await client.get('/internal/memory')
        assert response.status == 401
        response = await client.get('/internal/memory', headers={**auth, 'Origin': 'https://foreign.example'})
        assert response.status == 403
        response = await client.post('/internal/memory', json={'content': 'Mein Projekt heißt ORBIT und verwendet Java.'}, headers=auth)
        assert response.status == 200
        note = (await response.json())['fact']
        response = await client.post('/internal/generate', json={'text': 'Was weißt du über ORBIT?', 'conversation_id': 'room-memory-unit', 'request_id': str(uuid.uuid4())}, headers=auth)
        assert response.status == 200
        events = [json.loads(line) for line in (await response.text()).splitlines()]
        text = ''.join(item.get('text', '') for item in events)
        assert 'ORBIT' in text and '[DEMO]' in text
        assert events[-1]['type'] == 'done'
        snapshot = await (await client.get('/internal/memory?q=ORBIT', headers=auth)).json()
        assert snapshot['stats']['episodes'] == 1
        assert snapshot['episodes'][0]['conversation_id'] == 'room-memory-unit'
        response = await client.delete('/internal/memory/' + note['id'], headers=auth)
        assert (await response.json())['ok']
        # A fresh store instance sees the committed episode after the request/server lifecycle.
        persisted = memory.MemoryStore(tmp_path / 'memory.sqlite3')
        assert persisted.stats()['episodes'] == 1
        assert persisted.stats()['facts'] == 0
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_bridge_pre_cancel_and_invalid_body_are_safe(tmp_path, monkeypatch):
    monkeypatch.setenv('JARVIS_DATA_DIR', str(tmp_path))
    client = TestClient(TestServer(v3_bridge.create_app(TOKEN, demo=True)))
    await client.start_server()
    auth = {'Authorization': 'Bearer ' + TOKEN}
    try:
        response = await client.post('/internal/generate', json=[], headers=auth)
        assert response.status == 400
        response = await client.post('/internal/generate', json={'text': 'x', 'conversation_id': '../bad', 'request_id': 'invalid'}, headers=auth)
        assert response.status == 400
        response = await client.post('/internal/memory', json={'content': 'Passwort: should-not-persist'}, headers=auth)
        assert response.status == 400
        response = await client.post('/internal/cancel', json={'request_id': 'not-existing'}, headers=auth)
        assert (await response.json())['cancelled'] is False
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_bridge_tts_uses_existing_voice_adapter(tmp_path, monkeypatch):
    monkeypatch.setenv('JARVIS_DATA_DIR', str(tmp_path))
    from jarvis import tts
    calls = []
    monkeypatch.setattr(tts, 'synthesize', lambda text: calls.append(text) or b'unit-test-complete-mp3')
    client = TestClient(TestServer(v3_bridge.create_app(TOKEN, demo=True)))
    await client.start_server()
    try:
        response = await client.post('/internal/tts', json={'text': 'Hallo'}, headers={'Authorization': 'Bearer ' + TOKEN})
        assert response.status == 200
        assert response.headers['Content-Type'].startswith('audio/mpeg')
        assert await response.read() == b'unit-test-complete-mp3'
        assert calls == ['Hallo']
    finally:
        await client.close()


def test_live_bridge_preserves_durable_room_id(tmp_path, monkeypatch):
    monkeypatch.setenv('JARVIS_DATA_DIR', str(tmp_path))
    bridge = v3_bridge.Bridge(TOKEN, demo=False)
    try:
        agent = bridge.current_agent('raw-room-contract')
        assert agent.conversation_id == 'raw-room-contract'
        agent.memory_store.record_turn(agent.conversation_id, 'Lokaler Vertragstest', 'Keine API-Anfrage')
        assert len(agent.memory_store.recent_episodes('raw-room-contract')) == 1
        assert not agent.memory_store.recent_episodes('v3-raw-room-contract')
    finally:
        bridge.pool.shutdown(wait=False)
        bridge.cancel_pool.shutdown(wait=False)
        bridge.speech_pool.shutdown(wait=False)
        bridge.tts_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_bridge_transcribe_reports_measured_wait_and_decode(tmp_path, monkeypatch):
    """Die Regie-Kette braucht echte Zeiten: Bridge-Wartezeit und reines Dekodieren werden getrennt geliefert."""
    import io
    import wave
    monkeypatch.setenv('JARVIS_DATA_DIR', str(tmp_path))

    class FakeSegment:
        text = ' Hallo Jarvis '

    class FakeWhisper:
        def transcribe(self, audio, **kwargs):
            return [FakeSegment()], None

    class TimedBridge(v3_bridge.Bridge):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.whisper_models = {"base": FakeWhisper(), "small": FakeWhisper()}

    monkeypatch.setattr(v3_bridge, 'Bridge', TimedBridge)
    capture = io.BytesIO()
    with wave.open(capture, 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000)
        wav.writeframes(b'\x00\x00' * 3200)
    client = TestClient(TestServer(v3_bridge.create_app(TOKEN, demo=False)))
    await client.start_server()
    try:
        form = FormData()
        form.add_field('file', capture.getvalue(), filename='capture.wav', content_type='audio/wav')
        response = await client.post('/internal/transcribe', data=form, headers={'Authorization': 'Bearer ' + TOKEN})
        assert response.status == 200
        body = await response.json()
        assert 'Hallo' in body['text']
        assert body['bridge_wait_ms'] >= 0 and body['decode_ms'] >= 0
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_bridge_uebernimmt_routen_der_alten_oberflaeche(tmp_path, monkeypatch):
    """Etappe 3: Einstellungen, Echtzeit, Jobs, Protokolle und Ereignisse laufen über die Bridge (ohne 8765)."""
    monkeypatch.setenv('JARVIS_DATA_DIR', str(tmp_path))
    monkeypatch.delenv('JARVIS_NATIVE_UI', raising=False)
    from jarvis import runtime
    client = TestClient(TestServer(v3_bridge.create_app(TOKEN, demo=True)))
    await client.start_server()
    auth = {'Authorization': 'Bearer ' + TOKEN}
    try:
        status = await (await client.get('/internal/settings', headers=auth)).json()
        assert 'stimmen' in status and 'modell' in status
        assert (await client.post('/internal/settings', json={'tempo': 1.1}, headers=auth)).status == 403
        assert (await client.post('/internal/eleven/session', json={'pause': 1.0}, headers=auth)).status == 403
        tool = await (await client.post('/internal/tool', json={'name': 'mail_senden', 'args': {}}, headers=auth)).json()
        assert tool['ergebnis'].startswith('Demo')
        assert (await client.post('/internal/jobs/cancel', json={'id': '../x'}, headers=auth)).status == 400
        assert (await client.post('/internal/jobs/extern', json={'aktion': 'loeschen'}, headers=auth)).status == 400
        assert (await client.get('/internal/logs/geheim', headers=auth)).status == 404
        assert (await client.get('/internal/logs/ereignisse', headers=auth)).status == 200
        assert (await client.post('/internal/tts/preview', json={'text': ''}, headers=auth)).status == 400
        # Kamera-Ereignisse kommen aus dem Agenten im Bridge-Prozess, auch ohne native Oberfläche.
        start = (await (await client.get('/internal/events?after=0&timeout=0', headers=auth)).json())['seq']
        runtime.bus.publish({'typ': 'kamera', 'aktion': 'oeffnen', 'kamera': {'id': 'k1'}})
        runtime.bus.publish({'typ': 'antwort_text', 'text': 'privat'})
        events = await (await client.get(f'/internal/events?after={start}&timeout=1', headers=auth)).json()
        assert [e['typ'] for e in events['events']] == ['kamera']
        assert events['seq'] == start + 2
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_bridge_erkennt_aegyptisch_im_automatikmodus_mit_dem_genaueren_modell(tmp_path, monkeypatch):
    """Automatisch: base erkennt die Sprache (nur de/ar); arabische Sätze schreibt small mit ägyptischem Vorgabetext."""
    import io
    import wave
    monkeypatch.setenv('JARVIS_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('JARVIS_SPRACHE', 'auto')
    aufrufe = []

    class Seg:
        def __init__(self, text):
            self.text = text

    class Fake:
        def __init__(self, name):
            self.name = name
        def detect_language(self, audio):
            return "nl", 0.5, [("nl", 0.5), ("ar", 0.3), ("de", 0.2)]
        def transcribe(self, audio, **kwargs):
            aufrufe.append((self.name, kwargs["language"], kwargs["beam_size"], kwargs["initial_prompt"][:5]))
            return [Seg("فكرني أكلم فيبر")], None

    class B(v3_bridge.Bridge):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.whisper_models = {"base": Fake("base"), "small": Fake("small")}

    monkeypatch.setattr(v3_bridge, 'Bridge', B)
    capture = io.BytesIO()
    with wave.open(capture, 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000)
        wav.writeframes(b'\x00\x00' * 3200)
    client = TestClient(TestServer(v3_bridge.create_app(TOKEN, demo=False)))
    await client.start_server()
    try:
        form = FormData()
        form.add_field('file', capture.getvalue(), filename='capture.wav', content_type='audio/wav')
        response = await client.post('/internal/transcribe', data=form, headers={'Authorization': 'Bearer ' + TOKEN})
        body = await response.json()
        assert response.status == 200 and body['sprache'] == 'ar' and 'فيبر' in body['text']
        assert aufrufe == [("small", "ar", 3, "إزيك ")]  # Niederländisch wird nie gewählt
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_bridge_transcribe_liefert_entscheidung_des_tuerstehers(tmp_path, monkeypatch):
    """Mit pruefen=1 hängt die Bridge die Entscheidung des Sprach-Türstehers an; Kontext und Lage werden durchgereicht."""
    import io
    import wave
    from jarvis import sprachpruefung
    monkeypatch.setenv('JARVIS_DATA_DIR', str(tmp_path))
    aufrufe = []

    class Seg:
        text = ' Schatz, wo ist der Schlüssel? '
        avg_logprob = -0.2

    class Fake:
        def detect_language(self, audio):
            return "de", 0.9, [("de", 0.9)]
        def transcribe(self, audio, **kwargs):
            return [Seg()], None

    class B(v3_bridge.Bridge):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.whisper_models = {"base": Fake(), "small": Fake()}

    def pruefe(text, kontext, spricht):
        aufrufe.append((text, kontext, spricht))
        return {"aktion": "ignorieren", "grund": "nicht an Jarvis", "quelle": "typesafe", "werte": {}, "ms": 12}

    monkeypatch.setattr(v3_bridge, 'Bridge', B)
    monkeypatch.setattr(sprachpruefung, 'pruefe', pruefe)
    capture = io.BytesIO()
    with wave.open(capture, 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000)
        wav.writeframes(b'\x00\x00' * 3200)
    client = TestClient(TestServer(v3_bridge.create_app(TOKEN, demo=False)))
    await client.start_server()
    try:
        for felder, erwartet in (({}, None), ({"pruefen": "1", "kontext": "Du hast heute drei Termine", "jarvis_spricht": "1"}, "ignorieren")):
            form = FormData()
            form.add_field('file', capture.getvalue(), filename='capture.wav', content_type='audio/wav')
            for k, v in felder.items():
                form.add_field(k, v)
            response = await client.post('/internal/transcribe', data=form, headers={'Authorization': 'Bearer ' + TOKEN})
            body = await response.json()
            assert response.status == 200 and body.get("aktion") == erwartet
        assert aufrufe == [("Schatz, wo ist der Schlüssel?", "Du hast heute drei Termine", True)]
        assert body["grund"] == "nicht an Jarvis" and body["pruef_ms"] == 12
    finally:
        await client.close()
