"""Redigiertes Ereignisprotokoll (~/.jarvis/events.jsonl, Rechte 600): Modellaufrufe, Gedächtnissuchen,
Werkzeugausführungen, Kontextkürzung und Fehler – nur Metadaten, nie Inhalte.

Felder mit Inhaltsnamen (text, content, query, args, …) werden verworfen, übrige Werte geschwärzt
(Zugangsdaten-Muster) und gekürzt. Die Datei rotiert ab 5 MB (eine Vorgängerdatei bleibt). Fehler beim
Schreiben stoppen Jarvis nie.
"""

from __future__ import annotations

import datetime
import json
import threading

from . import config

_lock = threading.Lock()
_CONTENT_KEYS = {"text", "content", "query", "args", "argumente", "input", "prompt", "result", "ergebnis",
                 "message", "user_text", "assistant_text", "token", "secret", "password", "passwort"}
MAX_BYTES = 5 * 1024 * 1024


def path():
    return config.data_dir() / "events.jsonl"


def _clean(value):
    from .memory import _redact

    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _redact(str(value))[:160]


def log(kind: str, **fields) -> None:
    entry = {"zeit": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "art": str(kind)[:40]}
    for key, value in fields.items():
        if key.lower() in _CONTENT_KEYS:
            continue
        entry[str(key)[:40]] = _clean(value)
    try:
        with _lock:
            p = path()
            if p.exists() and p.stat().st_size > MAX_BYTES:
                p.replace(p.with_suffix(".jsonl.1"))
            new = not p.exists()
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            if new:
                p.chmod(0o600)
    except OSError:
        pass


def tail(limit: int = 50) -> list[dict]:
    try:
        lines = path().read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out
