"""Finite integration test against the running Java demo backend, with no external APIs.

Usage: .venv/bin/python tests/java_websocket_smoke.py http://127.0.0.1:8080
"""
from __future__ import annotations

import asyncio
import json
import math
import struct
import sys
import uuid

from aiohttp import ClientSession, WSMsgType


async def receive_until(ws, wanted):
    for _ in range(100):
        msg = await ws.receive(timeout=5)
        if msg.type != WSMsgType.TEXT:
            raise AssertionError(f"Unexpected socket message: {msg.type}")
        data = json.loads(msg.data)
        if data.get('type') == wanted:
            return data
    raise AssertionError(f"Missing event: {wanted}")


async def receive_audio(ws, stream_id):
    for _ in range(10):
        msg = await ws.receive(timeout=5)
        assert msg.type == WSMsgType.BINARY, msg
        length = struct.unpack('>I', msg.data[:4])[0]
        header = json.loads(msg.data[4:4 + length])
        payload = msg.data[4 + length:]
        if header['stream_id'] == stream_id:
            assert header['mime'] == 'audio/wav'
            assert payload[:4] == b'RIFF'
            return header
    raise AssertionError('No current-generation audio')


async def main(base):
    room = 'smoke-' + uuid.uuid4().hex[:20]
    owner, observer = 'owner-' + uuid.uuid4().hex[:18], 'observer-' + uuid.uuid4().hex[:18]
    headers = {'Origin': base}
    async with ClientSession(headers=headers) as session:
        async with session.get(base + '/api/config') as response:
            config = await response.json()
            assert response.status == 200
            assert config['demo'] is True
            assert len(config['cities']) >= 8
        async with session.get(base + '/') as response:
            assert response.status == 200
            assert 'id="app"' in await response.text()
        control = await session.ws_connect(f'{base}/ws/{room}/control?client={owner}&screen=main')
        welcome = await receive_until(control, 'WELCOME')
        assert welcome['role'] == 'owner'
        audio = await session.ws_connect(f'{base}/ws/{room}/audio?client={owner}')
        ops = await session.ws_connect(f'{base}/ws/{room}/control?client={observer}&screen=ops')
        assert (await receive_until(ops, 'WELCOME'))['role'] == 'observer'
        await control.send_json({'type':'USER_TEXT', 'text':'Erster Testzug'})
        first = await receive_until(control, 'TURN_START')
        header = await receive_audio(audio, first['stream_id'])
        assert header['sequence'] == 0
        await ops.send_json({'type':'STOP_STREAM', 'stream_id':None})
        assert 'Besitzer' in (await receive_until(ops, 'ERROR'))['message']
        await control.send_json({'type':'STOP_STREAM', 'stream_id':first['stream_id'], 'reason':'vad'})
        assert (await receive_until(control, 'STREAM_STOPPED'))['stream_id'] == first['stream_id']
        await control.send_json({'type':'USER_TEXT', 'text':'Zweiter Testzug nach Interrupt'})
        second = await receive_until(control, 'TURN_START')
        assert second['stream_id'] != first['stream_id']
        await receive_audio(audio, second['stream_id'])
        await control.send_json({'type':'STOP_STREAM', 'stream_id':first['stream_id'], 'reason':'stale'})
        await control.send_json({'type':'VIEW_SET', 'city_id':'tokyo'})
        assert (await receive_until(control, 'VIEW'))['city_id'] == 'tokyo'
        late_ops = await session.ws_connect(f'{base}/ws/{room}/control?client=lateops-{uuid.uuid4().hex[:16]}&screen=ops')
        snapshot = (await receive_until(late_ops, 'WELCOME'))['snapshot']
        assert snapshot['view']['city_id'] == 'tokyo'
        assert len(snapshot['transcripts']) >= 2
        assert 'jvm_heap_used_mb' in snapshot['metrics']
        utterance = str(uuid.uuid4())
        await audio.send_json({'type':'AUDIO_START', 'utterance_id':utterance, 'sample_rate':16000})
        await receive_until(control, 'STREAM_STOPPED')
        # Simulated PCM; the endpoint must process AUDIO_END rather than silently ignoring it.
        for _ in range(12):
            samples = [round(5000 * math.sin(2 * math.pi * 440 * index / 16000)) for index in range(320)]
            await audio.send_bytes(struct.pack('<320h', *samples))
        await audio.send_json({'type':'AUDIO_END', 'utterance_id':utterance})
        third = await receive_until(control, 'TURN_START')
        await receive_audio(audio, third['stream_id'])
        probe = await session.ws_connect(f'{base}/ws/{room}/control?client=probeops-{uuid.uuid4().hex[:16]}&screen=ops')
        current = (await receive_until(probe, 'WELCOME'))['snapshot']['transcripts']
        user = next(item for item in current if item['role'] == 'user' and item['stream_id'] == third['stream_id'])
        assert 'nicht transkribiert' in user['text']
        await probe.close()
        await control.close()
        await audio.close()
        await ops.close()
        await late_ops.close()
    print('JAVA WebSocket Smoke: owner/observer, audio, STOP, second turn, snapshot, PCM/STT: OK')


if __name__ == '__main__':
    asyncio.run(main((sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8080').rstrip('/')))
