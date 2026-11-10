"""Mikrofon -> vorallokierter Ringpuffer (nur RAM, wenige Sekunden). Keine Datei, kein Netz, keine Analyse im Callback.

Der Echtzeit-Callback kopiert nur die Samples in den Ringpuffer und setzt ein Ereignis. Alles andere läuft im
Arbeits-Thread des Dienstes. Gerätefehler, Stummschaltung oder fehlende Mikrofonfreigabe werden als ehrlicher
Zustand gemeldet (stille Daten über längere Zeit = vermutlich keine Freigabe).
"""

from __future__ import annotations

import threading

import numpy as np

from .detector import RATE

SECONDS = 4


class Ring:
    def __init__(self, seconds: int = SECONDS):
        self.data = np.zeros(RATE * seconds, dtype=np.float32)
        self.written = 0              # Gesamtzahl geschriebener Samples (monoton)
        self.lock = threading.Lock()
        self.ready = threading.Event()

    def write(self, block: np.ndarray) -> None:
        n = len(block)
        with self.lock:
            start = self.written % len(self.data)
            end = start + n
            if end <= len(self.data):
                self.data[start:end] = block
            else:
                cut = len(self.data) - start
                self.data[start:] = block[:cut]
                self.data[: n - cut] = block[cut:]
            self.written += n
        self.ready.set()

    def read(self, begin: int, count: int) -> np.ndarray | None:
        """Samples [begin, begin+count) – None, wenn schon überschrieben oder noch nicht vorhanden."""
        with self.lock:
            if begin < self.written - len(self.data) or begin + count > self.written or begin < 0:
                return None
            out = np.empty(count, dtype=np.float32)
            start = begin % len(self.data)
            end = start + count
            if end <= len(self.data):
                out[:] = self.data[start:end]
            else:
                cut = len(self.data) - start
                out[:cut] = self.data[start:]
                out[cut:] = self.data[: count - cut]
            return out

    def clear(self) -> None:
        with self.lock:
            self.data.fill(0)


class Capture:
    """Öffnet/schließt den Mikrofonstrom. stop() gibt das Mikrofon tatsächlich frei."""

    def __init__(self, ring: Ring, device: str | None = None):
        self.ring, self.device = ring, device
        self.stream = None
        self.status_flags = 0
        self.error: str | None = None

    def _callback(self, indata, frames, time_info, status):  # Echtzeit: nur kopieren
        if status:
            self.status_flags += 1
        self.ring.write(indata[:, 0])

    def start(self) -> bool:
        import sounddevice as sd

        if self.stream is not None:
            return True
        try:
            sd._terminate()
            sd._initialize()  # Geräteliste neu einlesen (Gerätewechsel, z. B. Kopfhörer)
            device = self.device if self.device else None
            self.stream = sd.InputStream(samplerate=RATE, channels=1, dtype="float32", blocksize=160,
                                         device=device, callback=self._callback)
            self.stream.start()
            self.error = None
            return True
        except Exception as exc:
            self.stream = None
            self.error = f"{type(exc).__name__}: {exc}"[:200]
            return False

    def stop(self) -> None:
        stream, self.stream = self.stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        self.ring.clear()

    @property
    def active(self) -> bool:
        return self.stream is not None and bool(getattr(self.stream, "active", False))
