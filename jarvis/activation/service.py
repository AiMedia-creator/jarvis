"""Bereitschaftsdienst: Mikrofon -> Impuls-Vorfilter -> YAMNet -> Entscheidung -> Jarvis öffnen.

Zustände (ehrlich, für Menüleiste und Status): AUS, BEREIT, PAUSIERT, SPRACHE_AKTIV (Mikrofon für das laufende
Jarvis-Gespräch freigegeben), KEIN_GERAET, KEIN_SIGNAL (lange nur Nullen: vermutlich keine Mikrofonfreigabe oder
stumm), KEIN_MODELL.

Audio-Übergabe: Spricht Jarvis gerade oder läuft ein Gespräch (Brücke /health -> audio), gibt der Dienst das Mikrofon
vollständig frei und übernimmt es erst nach einigen Sekunden Ruhe wieder. So können Jarvis' eigene Ausgabe und eine
Bestätigung nicht erneut auslösen, und Gesprächsaufnahme und Bereitschaft konkurrieren nicht.
Kein Audio wird gespeichert oder weitergegeben; nur Zähler und Zeiten werden erfasst.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .capture import Capture, Ring
from .detector import FRAME, PRE_ROLL, RATE, WINDOW, Debouncer, ImpulseFilter, decide
from .settings import Settings

RESUME_AFTER_S = 5.0
SILENCE_S = 15.0
RETRY_DEVICE_S = 5.0


def bridge_audio_state() -> dict:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8766/health", timeout=1) as r:
            return (json.loads(r.read()) or {}).get("audio") or {}
    except Exception:
        return {}


@dataclass
class Stats:
    kandidaten: int = 0
    erkannt: int = 0
    abgelehnt: int = 0
    unterdrueckt: int = 0
    ausgeloest: int = 0
    letzte_latenz_ms: int | None = None
    letztes_ergebnis: str = ""
    letzte_bewertung: dict = field(default_factory=dict)


class Service:
    def __init__(self, settings: Settings, classifier, *, ring: Ring | None = None, capture: Capture | None = None,
                 audio_state: Callable[[], dict] = bridge_audio_state, act: Callable[[str], str] | None = None,
                 clock: Callable[[], float] = time.monotonic):
        from . import launcher

        self.settings, self.classifier = settings, classifier
        self.ring = ring or Ring()
        self.capture = capture or Capture(self.ring, settings.input_device)
        self.audio_state, self.clock = audio_state, clock
        self.act = act or launcher.bring_up
        self.filter = ImpulseFilter()
        self.debounce = Debouncer(settings.cooldown_ms / 1000, settings.mode)
        self.state = "AUS" if not settings.enabled else "BEREIT"
        self.paused = False
        self.stats = Stats()
        self.cursor = 0
        self.candidates: list[tuple[int, float]] = []   # (Einsatz-Sample, Erkennungszeit)
        self.speech_until = 0.0
        self.last_signal = clock()
        self.last_device_try = -1e9
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.lock = threading.Lock()

    # --- Steuerung --------------------------------------------------------------------------------
    def pause(self) -> None:
        with self.lock:
            self.paused = True
            self.capture.stop()  # Mikrofon wirklich freigeben
            self.state = "PAUSIERT"

    def resume(self) -> None:
        with self.lock:
            self.paused = False
            self.state = "BEREIT"

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="jarvis-aktivierung", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.capture.stop()
        self.state = "AUS"

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.step()
            self.ring.ready.wait(0.05)
            self.ring.ready.clear()

    # --- ein Arbeitsschritt (auch direkt testbar) ---------------------------------------------------
    def step(self) -> None:
        with self.lock:
            if not self.settings.enabled:
                self.state = "AUS"
                return
            if self.paused:
                return
            if self.classifier is None:
                self.state = "KEIN_MODELL"
                return
            now = self.clock()
            audio = self.audio_state() or {}
            if audio.get("tts_aktiv") or audio.get("stt_aktiv"):
                self.speech_until = now + RESUME_AFTER_S
            if now < self.speech_until:
                if self.capture.active or self.state != "SPRACHE_AKTIV":
                    self.capture.stop()
                    self.candidates.clear()
                    self.cursor = self.ring.written
                self.state = "SPRACHE_AKTIV"
                return
            if not self.capture.active:
                if now - self.last_device_try < RETRY_DEVICE_S and self.state == "KEIN_GERAET":
                    return
                self.last_device_try = now
                if not self.capture.start():
                    self.state = "KEIN_GERAET"
                    return
                self.cursor = self.ring.written
                self.last_signal = now
                self.filter = ImpulseFilter()
                self.state = "BEREIT"
            self._consume(now)
            self._classify_ready(now)
            if self.state in ("BEREIT", "KEIN_SIGNAL"):
                self.state = "KEIN_SIGNAL" if now - self.last_signal > SILENCE_S else "BEREIT"

    def _consume(self, now: float) -> None:
        while self.cursor + FRAME <= self.ring.written:
            frame = self.ring.read(self.cursor, FRAME)
            if frame is None:  # zu weit zurückgefallen: auf aktuellen Stand springen
                self.cursor = self.ring.written - FRAME
                continue
            if np.any(frame != 0):
                self.last_signal = now
            onset = self.filter.push(frame)
            if onset is not None:
                absolute = self.cursor - (self.filter.index - 1) * FRAME + onset
                self.stats.kandidaten += 1
                self.candidates.append((absolute, now))
            self.cursor += FRAME

    def _classify_ready(self, now: float) -> None:
        remaining = []
        for onset, seen in self.candidates:
            begin = max(0, onset - PRE_ROLL)
            if begin + WINDOW > self.ring.written:
                remaining.append((onset, seen))
                continue
            window = self.ring.read(begin, WINDOW)
            if window is None:
                continue
            groups = self.classifier.group_scores(window)
            d = decide(groups, self.settings.triggers, self.settings.class_thresholds, self.settings.margin)
            self.stats.letzte_bewertung = {k: round(v, 3) for k, v in d.scores.items()} | {
                "gegenklasse": d.negative_name, "gegen": round(d.negative, 3)}
            if not d.accepted:
                self.stats.abgelehnt += 1
                continue
            self.stats.erkannt += 1
            if (self.audio_state() or {}).get("tts_aktiv"):
                self.stats.unterdrueckt += 1  # Jarvis spricht gerade: nie durch eigene Ausgabe auslösen
                continue
            event = self.debounce.offer(onset / RATE, now)
            if event is None:
                continue
            self.stats.ausgeloest += 1
            self.stats.letzte_latenz_ms = round((self.clock() - seen) * 1000 + (WINDOW - PRE_ROLL) / RATE * 1000)
            threading.Thread(target=self._fire, args=(event, d.trigger), daemon=True).start()
        self.candidates = remaining[-8:]

    def _fire(self, event: int, trigger: str) -> None:
        try:
            result = self.act(self.settings.url)
        except Exception as exc:
            result = f"fehler:{type(exc).__name__}"
        self.stats.letztes_ergebnis = f"#{event} {trigger}: {result}"
        from .. import events

        events.log("aktivierung", ereignis=event, ausloeser=trigger, ergebnis=result)

    def status(self) -> dict:
        return {"zustand": self.state, "pausiert": self.paused, "mikrofon_offen": self.capture.active,
                "geraet_fehler": self.capture.error, **self.stats.__dict__}
