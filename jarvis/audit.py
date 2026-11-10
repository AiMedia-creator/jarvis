"""Audit-Log: jeder Werkzeugaufruf als eine JSON-Zeile in ~/.jarvis/audit.jsonl (nur lokal, Rechte 600)."""

from __future__ import annotations

import datetime
import json
import threading

from . import config

_lock = threading.Lock()
_SECRET_WORDS = ("token", "passwort", "password", "secret", "hash", "api_key", "schluessel")


def path():
    return config.data_dir() / "audit.jsonl"


def _short(value, limit: int = 200):
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f"… (+{len(value) - limit} Zeichen)"
    return value


def record(source: str, tool: str, args: dict | None, status: str, result: str = "") -> None:
    """status: ok | fehler | freigabe_fehlt | abgebrochen"""
    from .memory import _redact

    clean = {k: _short(_redact(v) if isinstance(v, str) else v) for k, v in (args or {}).items()
             if not any(w in k.lower() for w in _SECRET_WORDS)}
    entry = {
        "zeit": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "quelle": source,
        "werkzeug": tool,
        "extern": "bestaetigt_vom_nutzer" in (args or {}),
        "freigabe": (args or {}).get("bestaetigt_vom_nutzer"),
        "argumente": clean,
        "status": status,
        "ergebnis": _short(_redact(str(result)), 300),
    }
    try:
        with _lock:
            p = path()
            new = not p.exists()
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
            if new:
                p.chmod(0o600)
    except OSError as exc:  # das Log darf Jarvis nie stoppen
        print(f"[audit] konnte nicht schreiben: {exc}")


def status_from_text(text: str, is_error: bool) -> str:
    if not is_error:
        return "ok"
    return "freigabe_fehlt" if "abgelehnt" in text.lower() else "fehler"


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
