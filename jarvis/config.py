"""Einstellungen und Schluessel.

Reihenfolge: Umgebungsvariable JARVIS_<NAME>, dann bekannte Standard-Variablen
(z. B. ANTHROPIC_API_KEY), dann die Datei ~/.jarvis.json.
Alle Daten (Profil, Gedaechtnis, Google-Token) liegen in ~/.jarvis/.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

CONFIG_PATH = Path.home() / ".jarvis.json"
DATA_DIR = Path.home() / ".jarvis"

# Einstellung -> zusaetzliche Umgebungsvariablen, die ebenfalls gelten
ENV_ALIASES = {
    "elevenlabs_key": ["ELEVENLABS_API_KEY"],
    "elevenlabs_voice": ["ELEVENLABS_VOICE_ID"],
    "hubspot_token": ["HUBSPOT_TOKEN", "HUBSPOT_ACCESS_TOKEN"],
    "anthropic_key": ["ANTHROPIC_API_KEY"],
    "alphavantage_api_key": ["ALPHAVANTAGE_API_KEY"],
}


def data_dir() -> Path:
    path = Path(os.getenv("JARVIS_DATA_DIR") or DATA_DIR).expanduser()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def load() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def anrede() -> str:
    """Wie Jarvis den Nutzer anspricht (Einstellung ``anrede`` bzw. JARVIS_ANREDE; Standard „Master“; leer = keine)."""
    value = (get("anrede", "Master") or "").strip()
    return value[:40] if value.replace(" ", "").replace("-", "").isalpha() else "Master"


def anrede_satz() -> str:
    """Zusatz für die Systemprompts. Die Anrede ändert nicht das Du und nicht die Haltung auf Augenhöhe."""
    name = anrede()
    return (f"\n\n# Anrede\nSprich den Nutzer mit „{name}“ an – am Anfang einer Antwort oder bei einer Rückfrage, "
            "nicht in jedem Satz. Du bleibst beim Du und sagst weiterhin offen deine Meinung.") if name else ""


def get(key: str, default: str | None = None) -> str | None:
    value = os.getenv(f"JARVIS_{key.upper()}")
    if not value:
        for name in ENV_ALIASES.get(key, []):
            value = os.getenv(name)
            if value:
                break
    return value or load().get(key) or default


def save(key: str, value: str | None) -> None:
    data = load()
    if value is None:
        data.pop(key, None)
    else:
        data[key] = value
    CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(CONFIG_PATH, 0o600)  # enthaelt Schluessel: nur du darfst lesen
