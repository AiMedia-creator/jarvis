"""Sprachausgabe: ElevenLabs oder kostenlose Microsoft-Stimmen (edge-tts).

Die gewaehlte Stimme steht in ~/.jarvis.json ("voice"), z. B. "edge:de-DE-ConradNeural"
oder "eleven:JBFqnCBsd6RMkjVDRZzb". Tempo: "voice_speed" (0.8 bis 1.3).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import time
from typing import Iterator

import requests

from . import config, sprache

ELEVEN_API = "https://api.elevenlabs.io/v1"
DEFAULT_EDGE = "edge:de-DE-ConradNeural"

EDGE_VOICES = [
    ("edge:de-DE-ConradNeural", "Conrad – männlich, ruhig"),
    ("edge:de-DE-KillianNeural", "Killian – männlich, jung"),
    ("edge:de-DE-FlorianMultilingualNeural", "Florian – männlich, warm, sehr natürlich"),
    ("edge:de-AT-JonasNeural", "Jonas – männlich, österreichisch"),
    ("edge:de-CH-JanNeural", "Jan – männlich, schweizerisch"),
    ("edge:de-DE-SeraphinaMultilingualNeural", "Seraphina – weiblich, sehr natürlich"),
    ("edge:de-DE-KatjaNeural", "Katja – weiblich, klar"),
    ("edge:de-DE-AmalaNeural", "Amala – weiblich, freundlich"),
    ("edge:en-GB-RyanNeural", "Ryan – britisch (Film-Jarvis, spricht Deutsch mit Akzent)"),
    ("edge:ar-EG-ShakirNeural", "Shakir – ägyptisch, männlich (für arabische Antworten)"),
    ("edge:ar-EG-SalmaNeural", "Salma – ägyptisch, weiblich (für arabische Antworten)"),
]
# Fertige ElevenLabs-Stimmen (im Gratis-Tarif per API nutzbar), sprechen mit dem Multilingual-Modell Deutsch.
ELEVEN_VOICES = [
    ("eleven:JBFqnCBsd6RMkjVDRZzb", "George – warm, britisch"),
    ("eleven:onwK4e9ZLuTAKqWW03F9", "Daniel – britisch, sachlich (am meisten Jarvis)"),
    ("eleven:nPczCjzI2devNBz1zQrb", "Brian – tief, ruhig"),
    ("eleven:EXAVITQu4vr4xnSDxMaL", "Sarah – weiblich, professionell"),
    ("eleven:pFZP5JQG7iQjIQuC4Bku", "Lily – weiblich, britisch"),
]

# Wenn ElevenLabs den Schluessel ablehnt, nicht bei jedem Satz neu versuchen (das kostet Zeit).
eleven_problem: str | None = None
# Eine dauerhafte HTTPS-Verbindung: spart pro Satz den TLS-Verbindungsaufbau (oft 100–300 ms).
_session: requests.Session | None = None


def _http() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=2, pool_maxsize=4)
        _session.mount("https://", adapter)
    return _session


def has_eleven() -> bool:
    return bool(config.get("elevenlabs_key")) and eleven_problem is None


def voices() -> list[dict]:
    out = [{"id": i, "name": n, "gruppe": "Microsoft (kostenlos)"} for i, n in EDGE_VOICES]
    if config.get("elevenlabs_key"):
        out += [{"id": i, "name": n, "gruppe": "ElevenLabs"} for i, n in ELEVEN_VOICES]
    return out


def current_voice() -> str:
    v = config.get("voice")
    if v:
        return v
    if config.get("elevenlabs_key"):
        return "eleven:" + (config.get("elevenlabs_voice") or "JBFqnCBsd6RMkjVDRZzb")
    return DEFAULT_EDGE


def speed() -> float:
    try:
        return max(0.8, min(1.3, float(config.get("voice_speed", "1.1") or 1.1)))
    except ValueError:
        return 1.1


def _spoken_text(text: str) -> str:
    code = sprache.text_sprache(text)
    if code == "ar":
        return sprache.fuer_sprache(text)
    if code == "en":
        return sprache.fuer_englisch(text)
    from .speech import for_speech

    return for_speech(text)


def _edge_fallback(text: str) -> str:
    """Wählt nach einem ElevenLabs-Ausfall eine lokale Stimme, die zur Ausgabe passt."""
    code = sprache.text_sprache(text)
    if code == "ar":
        selected = str(config.get("stimme_arabisch", sprache.STIMME_ARABISCH) or sprache.STIMME_ARABISCH)
        return selected if selected.startswith("edge:") else sprache.STIMME_ARABISCH
    if code == "en":
        return sprache.STIMME_ENGLISCH
    return DEFAULT_EDGE


_audio_cache: "OrderedDict[tuple, bytes]" = None  # type: ignore[assignment]


def synthesize(text: str, voice: str | None = None) -> bytes:
    """Mit kleinem Zwischenspeicher: haeufige Saetze ("Moment, ich schaue nach.") kommen sofort."""
    global _audio_cache
    from collections import OrderedDict

    voice = sprache.stimme_fuer(text, voice or current_voice())
    text = _spoken_text(text)
    if _audio_cache is None:
        _audio_cache = OrderedDict()
    key = (text, voice, speed())
    if key in _audio_cache:
        _audio_cache.move_to_end(key)
        return _audio_cache[key]
    audio = _synthesize(text, voice)
    _audio_cache[key] = audio
    while len(_audio_cache) > 64:
        _audio_cache.popitem(last=False)
    return audio


def _synthesize(text: str, voice: str | None = None) -> bytes:
    global eleven_problem
    v = voice or current_voice()
    if v.startswith("eleven:"):
        if has_eleven():
            try:
                return _eleven(text, v.split(":", 1)[1])
            except requests.HTTPError as exc:
                code = exc.response.status_code if exc.response is not None else 0
                if code in (401, 402, 403):
                    eleven_problem = f"ElevenLabs lehnt ab ({code}) – Schlüssel prüfen. Nutze kostenlose Stimme."
                    print(f"[stimme] {eleven_problem}")
                else:
                    print(f"[stimme] ElevenLabs-Fehler {code}, kostenlose Stimme für diesen Satz.")
            except Exception as exc:
                print(f"[stimme] ElevenLabs nicht erreichbar ({exc}), kostenlose Stimme für diesen Satz.")
        v = _edge_fallback(text)
    return _edge(text, v.split(":", 1)[1] if v.startswith("edge:") else v)


def _eleven(text: str, voice_id: str) -> bytes:
    headers = {"xi-api-key": config.get("elevenlabs_key") or ""}
    model = config.get("elevenlabs_model", "eleven_flash_v2_5")  # Flash = schnellste Antwortzeit
    body = {"text": text, "model_id": model, "language_code": sprache.sprachcode(text),
            "voice_settings": {"speed": max(0.7, min(1.2, speed()))}}
    url = f"{ELEVEN_API}/text-to-speech/{voice_id}"
    params = {"output_format": "mp3_44100_128"}
    http = _http()
    r = http.post(url, params=params, headers=headers, json=body, timeout=15)
    if r.status_code == 400:  # manche Modelle kennen language_code/speed nicht
        body = {"text": text, "model_id": model}
        r = http.post(url, params=params, headers=headers, json=body, timeout=15)
    r.raise_for_status()
    return r.content


def _edge(text: str, voice_id: str) -> bytes:
    import edge_tts

    rate = f"{round((speed() - 1) * 100):+d}%"

    async def collect() -> bytes:
        audio = bytearray()
        async for chunk in edge_tts.Communicate(text, voice_id, rate=rate).stream():
            if chunk["type"] == "audio":
                audio.extend(chunk["data"])
        if not audio:
            raise RuntimeError("kein Ton empfangen")
        return bytes(audio)

    async def run() -> bytes:
        return await asyncio.wait_for(collect(), timeout=15)  # nie ewig auf den Sprachdienst warten

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(run())
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        return pool.submit(asyncio.run, run()).result()


# --- Echtes Streaming (Satz für Satz, in kleinen PCM-Stücken) ---------------------------------------------

PCM_RATE = 22050
PCM_MIME = f"audio/pcm;rate={PCM_RATE}"
CHUNK_BYTES = PCM_RATE * 2 // 5          # 200 ms 16-bit Mono
STREAM_TIMEOUT = (5, 10)                  # Verbindungsaufbau, max. Pause zwischen zwei Stücken
_stream_cache: "OrderedDict[tuple, bytes]" = None  # type: ignore[assignment]
_stream_lock = threading.Lock()


def _rechunk(chunks: Iterator[bytes], cancel: threading.Event | None) -> Iterator[bytes]:
    buf = bytearray()
    for chunk in chunks:
        if cancel is not None and cancel.is_set():
            return
        buf.extend(chunk)
        while len(buf) >= CHUNK_BYTES:
            yield bytes(buf[:CHUNK_BYTES])
            del buf[:CHUNK_BYTES]
    if buf and not (cancel is not None and cancel.is_set()):
        yield bytes(buf[: len(buf) - len(buf) % 2])  # nur ganze 16-bit-Samples


def _eleven_stream(text: str, voice_id: str, cancel: threading.Event | None) -> Iterator[bytes]:
    headers = {"xi-api-key": config.get("elevenlabs_key") or ""}
    model = config.get("elevenlabs_model", "eleven_flash_v2_5")
    url = f"{ELEVEN_API}/text-to-speech/{voice_id}/stream"
    params = {"output_format": f"pcm_{PCM_RATE}"}
    body = {"text": text, "model_id": model, "language_code": sprache.sprachcode(text),
            "voice_settings": {"speed": max(0.7, min(1.2, speed()))}}
    http = _http()
    r = http.post(url, params=params, headers=headers, json=body, timeout=STREAM_TIMEOUT, stream=True)
    if r.status_code == 400:  # manche Modelle kennen language_code/speed nicht
        r.close()
        r = http.post(url, params=params, headers=headers, json={"text": text, "model_id": model},
                      timeout=STREAM_TIMEOUT, stream=True)
    try:
        r.raise_for_status()
        yield from _rechunk(r.iter_content(chunk_size=4096), cancel)
    finally:
        r.close()  # bei Abbruch: Verbindung zum Anbieter sofort schliessen


def stream(text: str, voice: str | None = None, cancel: threading.Event | None = None) -> tuple[str, Iterator[bytes]]:
    """Liefert (Format, Stücke). ElevenLabs: PCM in 200-ms-Stücken, sobald sie kommen. Kostenlose Stimme oder
    Ausfall vor dem ersten Stück: ein ganzes MP3 (kein Streaming moeglich). Bricht ElevenLabs mitten im Satz ab,
    endet der Satz (kein Wechsel der Stimme mitten im Satz)."""
    global eleven_problem, _stream_cache
    from collections import OrderedDict

    spoken = _spoken_text(text)
    v = sprache.stimme_fuer(text, voice or current_voice())
    key = (spoken, v, speed())
    with _stream_lock:
        if _stream_cache is None:
            _stream_cache = OrderedDict()
        cached = _stream_cache.get(key)
    if cached is not None:
        return PCM_MIME, iter([cached[i:i + CHUNK_BYTES] for i in range(0, len(cached), CHUNK_BYTES)])
    if v.startswith("eleven:") and has_eleven():
        source = _eleven_stream(spoken, v.split(":", 1)[1], cancel)
        try:
            first = next(source)
        except StopIteration:
            first = None
        except requests.HTTPError as exc:
            code = exc.response.status_code if exc.response is not None else 0
            if code in (401, 402, 403):
                eleven_problem = f"ElevenLabs lehnt ab ({code}) – Schlüssel prüfen. Nutze kostenlose Stimme."
            first = None
        except Exception:
            first = None
        if first is not None:
            def chunks() -> Iterator[bytes]:
                collected = bytearray(first)
                yield first
                complete = False
                try:
                    for piece in source:
                        collected.extend(piece)
                        yield piece
                    complete = not (cancel is not None and cancel.is_set())
                except Exception as exc:  # mitten im Satz: Satz endet hier, Antwort läuft weiter
                    print(f"[stimme] Stream abgebrochen ({type(exc).__name__}).")
                finally:
                    source.close()
                    if complete and len(collected) < 4_000_000:
                        with _stream_lock:
                            _stream_cache[key] = bytes(collected)
                            while len(_stream_cache) > 32:
                                _stream_cache.popitem(last=False)
            return PCM_MIME, chunks()
    fallback = _edge_fallback(text) if v.startswith("eleven:") else v
    return "audio/mpeg", iter([_edge(spoken, fallback.split(":", 1)[1] if fallback.startswith("edge:") else fallback)])
