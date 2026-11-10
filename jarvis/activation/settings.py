"""Einstellungen der akustischen Aktivierung (~/.jarvis/aktivierung.json). Standard: AUS bis zum Opt-in."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from .. import config

MODEL_FILE = "yamnet.tflite"
CLASSMAP_FILE = "yamnet_class_map.csv"
MODEL_URL = "https://storage.googleapis.com/mediapipe-models/audio_classifier/yamnet/float32/1/yamnet.tflite"
CLASSMAP_URL = "https://raw.githubusercontent.com/tensorflow/models/master/research/audioset/yamnet/yamnet_class_map.csv"
MODEL_SHA256 = "4d8b4a53282dc83ef04e3e7dbc4fbc98082e34e44ed798e16c3a0cdd4c584faf"


@dataclass
class Settings:
    enabled: bool = False                       # erst nach ausdrücklichem Opt-in
    triggers: list = field(default_factory=lambda: ["clap", "snap"])
    mode: str = "single"                        # "single" = einmal; "double" = zweimal innerhalb 0,9 s
    cooldown_ms: int = 3000
    input_device: str | None = None             # Gerätename; None = Systemstandard
    class_thresholds: dict = field(default_factory=lambda: {"clap": 0.30, "snap": 0.30})
    margin: float = 0.10                        # Abstand zur stärksten Gegenklasse (Tippen, Sprache, Musik ...)
    calibration_profile: dict = field(default_factory=dict)
    foreground_policy: str = "fokus_oder_oeffnen"   # vorhandenes Fenster fokussieren, sonst einmal öffnen
    start_listening_after_open: bool = False    # nicht automatisch Mikrofon/Aufnahme starten
    confirmation_tone: bool = False
    url: str = "http://localhost:8080"

    @classmethod
    def load(cls) -> "Settings":
        try:
            raw = json.loads((config.data_dir() / "aktivierung.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        known = {k: v for k, v in (raw if isinstance(raw, dict) else {}).items() if k in cls.__dataclass_fields__}
        s = cls(**known)
        s.triggers = [t for t in s.triggers if t in ("clap", "snap")] or ["clap", "snap"]
        s.mode = s.mode if s.mode in ("single", "double") else "single"
        s.cooldown_ms = max(1000, min(60000, int(s.cooldown_ms)))
        if not str(s.url).startswith(("http://localhost:", "http://127.0.0.1:")):
            s.url = "http://localhost:8080"  # nur die bekannte lokale Jarvis-Adresse
        return s

    def save(self) -> None:
        path = config.data_dir() / "aktivierung.json"
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        path.chmod(0o600)


def model_dir():
    path = config.data_dir() / "models"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path
