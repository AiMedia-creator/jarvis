"""Befehle: python -m jarvis.activation setup | start | status | aus

setup   Opt-in, Modell laden (einmalig, mit Prüfsumme), mit deinen eigenen Klatschern/Schnipsern kalibrieren.
start   Bereitschaft mit Menüleisten-Symbol starten (nur wenn eingeschaltet).
status  Einstellungen und Kalibrierung anzeigen.
aus     Bereitschaft ausschalten (enabled=false).
"""

from __future__ import annotations

import hashlib
import sys
import time
import urllib.request

import numpy as np

from .detector import FRAME, PRE_ROLL, RATE, WINDOW, Classifier, ImpulseFilter, calibrate
from .settings import CLASSMAP_FILE, CLASSMAP_URL, MODEL_FILE, MODEL_SHA256, MODEL_URL, Settings, model_dir


def load_classifier() -> Classifier | None:
    d = model_dir()
    if not (d / MODEL_FILE).exists() or not (d / CLASSMAP_FILE).exists():
        return None
    return Classifier(str(d / MODEL_FILE), str(d / CLASSMAP_FILE))


def download_model() -> None:
    d = model_dir()
    for url, name in ((MODEL_URL, MODEL_FILE), (CLASSMAP_URL, CLASSMAP_FILE)):
        target = d / name
        if target.exists():
            continue
        print(f"Lade {name} …")
        data = urllib.request.urlopen(url, timeout=60).read()
        if name == MODEL_FILE and hashlib.sha256(data).hexdigest() != MODEL_SHA256:
            raise SystemExit("Prüfsumme des Modells stimmt nicht – abgebrochen, nichts gespeichert.")
        target.write_bytes(data)
        target.chmod(0o600)


def record(seconds: float) -> np.ndarray:
    import sounddevice as sd

    audio = sd.rec(int(seconds * RATE), samplerate=RATE, channels=1, dtype="float32")
    sd.wait()
    return audio[:, 0]  # bleibt nur im Arbeitsspeicher


def impulses(signal: np.ndarray) -> list[int]:
    f = ImpulseFilter()
    found = []
    for i in range(0, len(signal) - FRAME, FRAME):
        onset = f.push(signal[i:i + FRAME])
        if onset is not None:
            found.append(onset)
    return found


def scores_for(classifier: Classifier, signal: np.ndarray, group: str) -> list[float]:
    out = []
    padded = np.concatenate([np.zeros(PRE_ROLL, np.float32), signal, np.zeros(WINDOW, np.float32)])
    for onset in impulses(signal):
        window = padded[onset: onset + WINDOW]
        out.append(classifier.group_scores(window)[group])
    return out


def setup() -> int:
    s = Settings.load()
    print(__doc__)
    print("Die Bereitschaft hört dauerhaft lokal auf das Mikrofon, solange sie eingeschaltet ist. Es wird nichts "
          "gespeichert oder hochgeladen; ein Symbol in der Menüleiste zeigt den Zustand, Pause gibt das Mikrofon frei.")
    if input("Bereitschaft für Klatschen/Schnipsen einschalten? (ja/nein) ").strip().lower() not in ("ja", "j", "yes", "y"):
        print("Nichts geändert.")
        return 1
    download_model()
    classifier = load_classifier()
    input("Kalibrierung 1/3: 5 Sekunden Ruhe (normale Umgebung, gern tippen oder reden). Enter zum Start …")
    background = record(5)
    neg = scores_for(classifier, background, "clap") + scores_for(classifier, background, "snap")
    results = {}
    for step, (group, word) in enumerate((("clap", "KLATSCHEN"), ("snap", "SCHNIPSEN")), start=2):
        input(f"Kalibrierung {step}/3: Bitte 3-mal {word}, mit je 2 Sekunden Pause (8 Sekunden). Enter zum Start …")
        pos = scores_for(classifier, record(8), group)
        results[group] = pos
        print(f"  erkannt: {len(pos)} Impulse, Werte {[round(p, 2) for p in pos]}")
    for group in ("clap", "snap"):
        s.class_thresholds[group] = round(calibrate(results.get(group, []), neg), 3)
    s.calibration_profile = {"zeit": time.strftime("%Y-%m-%d %H:%M"), "stoer_max": round(max(neg), 3) if neg else 0.0,
                             "klatschen": [round(x, 3) for x in results.get("clap", [])],
                             "schnipsen": [round(x, 3) for x in results.get("snap", [])],
                             "modell": "yamnet float32 (MediaPipe) " + MODEL_SHA256[:12]}
    s.enabled = True
    s.save()
    weak = [g for g in ("clap", "snap") if len(results.get(g, [])) < 2]
    print(f"Fertig. Schwellen: {s.class_thresholds}.")
    if weak:
        print(f"Hinweis: Für {', '.join(weak)} wurden kaum Impulse erkannt – lauter/näher versuchen und Setup wiederholen.")
    print("Start: python -m jarvis.activation start (oder automatisch bei der Anmeldung über die Start-App).")
    return 0


def start() -> int:
    from .menubar import run
    from .service import Service

    s = Settings.load()
    if not s.enabled:
        print("Die akustische Aktivierung ist aus. Einschalten mit: python -m jarvis.activation setup")
        return 1
    service = Service(s, load_classifier())
    service.start()
    run(service)
    return 0


def status() -> int:
    s = Settings.load()
    print(f"Eingeschaltet: {s.enabled} | Auslöser: {s.triggers} | Modus: {s.mode} | Cooldown: {s.cooldown_ms} ms")
    print(f"Schwellen: {s.class_thresholds} | Abstand: {s.margin} | Modell: {'vorhanden' if load_classifier() else 'fehlt'}")
    print(f"Kalibrierung: {s.calibration_profile or 'keine'}")
    return 0


def off() -> int:
    s = Settings.load()
    s.enabled = False
    s.save()
    print("Akustische Aktivierung ausgeschaltet.")
    return 0


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "status"
    sys.exit({"setup": setup, "start": start, "status": status, "aus": off}.get(command, status)())
