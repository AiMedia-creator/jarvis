"""Erkennung: DSP-Impuls-Vorfilter -> lokales YAMNet (nur für Kandidaten) -> kalibrierte Entscheidung.

Kein Whisper, keine Cloud, kein LLM pro Frame. YAMNet (521 AudioSet-Klassen, 16 kHz Mono, Fenster 0,975 s) läuft nur,
wenn der Vorfilter einen kurzen, kräftigen Impuls findet. Entschieden wird über Klassengruppen mit kalibrierter
Schwelle UND Abstand zur stärksten Gegenklasse – nicht über eine feste Lautstärke.
"""

from __future__ import annotations

import csv
import itertools
from dataclasses import dataclass

import numpy as np

RATE = 16000
FRAME = 160                    # 10 ms
WINDOW = 15600                 # YAMNet-Eingang (0,975 s)
PRE_ROLL = 2400                # 150 ms vor dem Einsatz mit ins Fenster
GROUPS = {
    "clap": ("Clapping", "Hands", "Slap, smack", "Applause"),
    "snap": ("Finger snapping",),
}
NEGATIVE = ("Speech", "Typing", "Computer keyboard", "Typewriter", "Clicking", "Clickety-clack", "Knock", "Door",
            "Tap", "Music", "Tick", "Mouse", "Writing", "Cutlery, silverware", "Dishes, pots, and pans",
            "Footsteps", "Slam", "Bouncing", "Thump, thud", "Laughter", "Cough", "Television", "Radio")
_ids = itertools.count(1)


class ImpulseFilter:
    """Findet kurze, kräftige Impulse (Klatschen/Schnipsen) relativ zum laufend geschätzten Grundrauschen.

    Kandidat, wenn ein 10-ms-Frame deutlich über dem Grundrauschen liegt, schnell ansteigt und innerhalb von
    ~120 ms wieder stark abfällt. Tippen und Sprache sind oft leiser oder länger; die Feinunterscheidung macht YAMNet.
    """

    def __init__(self, ratio: float = 8.0, min_level: float = 0.02, decay_db: float = 10.0):
        self.ratio, self.min_level, self.decay = ratio, min_level, 10 ** (-decay_db / 20)
        self.floor = 0.003
        self.pending: tuple[int, float] | None = None   # (Frame-Index des Einsatzes, Spitzenpegel)
        self.prev = 0.0
        self.index = 0

    def push(self, frame: np.ndarray) -> int | None:
        """Ein 10-ms-Frame; gibt den Sample-Index des bestätigten Impuls-Einsatzes zurück (sonst None)."""
        rms = float(np.sqrt(np.mean(np.square(frame, dtype=np.float32))) + 1e-9)
        peak = float(np.max(np.abs(frame)))
        i = self.index
        self.index += 1
        result = None
        if self.pending is not None:
            onset, top = self.pending
            if i - onset >= 12:  # 120 ms nach dem Einsatz: muss deutlich abgeklungen sein
                if rms <= top * self.decay:
                    result = onset * FRAME
                self.pending = None
        elif peak >= max(self.min_level, self.floor * self.ratio * 3) and rms >= max(self.min_level / 3, self.floor * self.ratio) \
                and rms >= self.prev * 3:
            self.pending = (i, rms)
        if self.pending is None or self.pending[0] != i:
            self.floor = 0.995 * self.floor + 0.005 * min(rms, self.floor * 4 + 1e-4)  # langsam, Impulse kaum
        self.prev = rms
        return result


@dataclass
class Decision:
    accepted: bool
    trigger: str | None
    score: float
    negative: float
    negative_name: str
    scores: dict


class Classifier:
    """YAMNet über LiteRT (ohne TensorFlow). Modell wird nur beim Setup heruntergeladen; danach offline."""

    def __init__(self, model_path: str, classmap_path: str):
        from ai_edge_litert.interpreter import Interpreter

        self.names = [row[2] for row in csv.reader(open(classmap_path, encoding="utf-8"))][1:]
        self.index = {n: i for i, n in enumerate(self.names)}
        self.interpreter = Interpreter(model_path=model_path)
        self.interpreter.allocate_tensors()
        self.inp = self.interpreter.get_input_details()[0]["index"]
        self.out = self.interpreter.get_output_details()[0]["index"]

    def scores(self, window: np.ndarray) -> np.ndarray:
        x = np.zeros(WINDOW, dtype=np.float32)
        x[: min(WINDOW, len(window))] = window[:WINDOW]
        self.interpreter.set_tensor(self.inp, x)
        self.interpreter.invoke()
        return self.interpreter.get_tensor(self.out)[0]

    def group_scores(self, window: np.ndarray) -> dict:
        s = self.scores(window)
        out = {g: float(max(s[self.index[n]] for n in names if n in self.index)) for g, names in GROUPS.items()}
        neg = [(float(s[self.index[n]]), n) for n in NEGATIVE if n in self.index]
        out["_negative"], out["_negative_name"] = max(neg) if neg else (0.0, "")
        return out


def decide(groups: dict, triggers: list, thresholds: dict, margin: float) -> Decision:
    """Bester erlaubter Auslöser muss die kalibrierte Schwelle UND den Abstand zur stärksten Gegenklasse schaffen."""
    candidates = [(groups.get(t, 0.0), t) for t in triggers]
    score, trigger = max(candidates) if candidates else (0.0, None)
    negative = groups.get("_negative", 0.0)
    ok = trigger is not None and score >= float(thresholds.get(trigger, 0.3)) and score - negative >= margin
    return Decision(ok, trigger if ok else None, score, negative, groups.get("_negative_name", ""),
                    {k: v for k, v in groups.items() if not k.startswith("_")})


class Debouncer:
    """Überlappende Fenster -> ein Ereignis mit ID; Cooldown gegen Mehrfachöffnen; optional Doppelmodus."""

    def __init__(self, cooldown_s: float, mode: str = "single", double_window_s: float = 0.9):
        self.cooldown, self.mode, self.double_window = cooldown_s, mode, double_window_s
        self.last_fire = -1e9
        self.last_hit = -1e9
        self.last_onset = -1e9

    def offer(self, onset_s: float, now_s: float) -> int | None:
        if onset_s - self.last_onset < 0.25:   # gleicher Impuls in überlappendem Fenster
            return None
        self.last_onset = onset_s
        if now_s - self.last_fire < self.cooldown:
            return None
        if self.mode == "double":
            if not 0.2 <= onset_s - self.last_hit <= self.double_window:
                self.last_hit = onset_s
                return None
            self.last_hit = -1e9
        self.last_fire = now_s
        return next(_ids)


def calibrate(positives: list[float], negatives: list[float], floor: float = 0.12, ceiling: float = 0.6) -> float:
    """Schwelle aus eigenen Beispielen: unter den eigenen Treffern, aber über den Störgeräuschen (begrenzt)."""
    if not positives:
        return 0.3
    low_pos = float(np.percentile(positives, 20)) * 0.8
    high_neg = max(negatives) * 1.15 if negatives else 0.0
    return float(min(ceiling, max(floor, low_pos, high_neg)))
