"""Dauerhafte Ablage fuer Jobs und Freigaben in der vorhandenen SQLite/WAL-Datenbank (memory.sqlite3).

Write-through: jede Aenderung wird sofort gespeichert. Beim Start stellt restore() den letzten Stand wieder her:
- Jobs, die beim Absturz/Neustart noch liefen, werden FAILED mit Grund "Neustart" (sie laufen nicht weiter).
- Offene Vorlagen bleiben offen (Ablauf nach TTL wie gehabt).
- Freigaben, die gerade ausgefuehrt wurden (RUNNING), werden NICHT erneut ausgefuehrt, sondern als UNKLAR
  markiert: ob die Mail/der Termin rausging, muss im Zielsystem geprueft werden.
Fehler beim Speichern duerfen Jarvis nie stoppen; sie werden nur protokolliert.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from . import config

KEEP_JOBS = 500
KEEP_APPROVALS = 500
_lock = threading.Lock()
_ready: set[str] = set()


def db_path() -> Path:
    return config.data_dir() / "memory.sqlite3"


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(path), timeout=10.0, isolation_level=None)
    try:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=10000")
        c.execute("PRAGMA synchronous=NORMAL")
        if str(path) not in _ready:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                  id TEXT PRIMARY KEY, status TEXT NOT NULL, start REAL NOT NULL,
                  updated REAL NOT NULL, data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS jobs_start ON jobs(start);
                CREATE TABLE IF NOT EXISTS approvals (
                  id TEXT PRIMARY KEY, status TEXT NOT NULL, created REAL NOT NULL,
                  updated REAL NOT NULL, data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS approvals_created ON approvals(created);
                CREATE TABLE IF NOT EXISTS tool_ledger (
                  id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, tool TEXT NOT NULL, kind TEXT NOT NULL,
                  status TEXT NOT NULL, source TEXT NOT NULL, conversation TEXT NOT NULL, turn INTEGER,
                  started REAL NOT NULL, ended REAL, result TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS tool_ledger_fp ON tool_ledger(fingerprint, started DESC);
                CREATE INDEX IF NOT EXISTS tool_ledger_turn ON tool_ledger(conversation, turn);
                """
            )
            _ready.add(str(path))
        yield c
    finally:
        c.close()


def _write(table: str, row_id: str, status: str, created: float, data: dict) -> None:
    keep = KEEP_JOBS if table == "jobs" else KEEP_APPROVALS
    created_col = "start" if table == "jobs" else "created"
    try:
        payload = json.dumps(data, ensure_ascii=False, default=str)
        with _lock, _connect() as c:
            c.execute(f"INSERT INTO {table} (id, status, {created_col}, updated, data) VALUES (?, ?, ?, ?, ?) "
                      f"ON CONFLICT(id) DO UPDATE SET status=excluded.status, updated=excluded.updated, "
                      f"data=excluded.data", (row_id, status, created, time.time(), payload))
            c.execute(f"DELETE FROM {table} WHERE id IN (SELECT id FROM {table} ORDER BY {created_col} DESC "
                      f"LIMIT -1 OFFSET ?)", (keep,))
    except (sqlite3.Error, OSError, TypeError, ValueError) as exc:
        print(f"[persist] {table} {row_id} nicht gespeichert: {type(exc).__name__}")


def save_job(job: dict) -> None:
    _write("jobs", job["id"], job["status"], float(job["start"]), job)


def save_approval(item: dict) -> None:
    data = {k: v for k, v in item.items() if not k.startswith("_")}
    _write("approvals", item["id"], item["status"], float(item["erstellt"]), data)


def _load(table: str, limit: int) -> list[dict]:
    created_col = "start" if table == "jobs" else "created"
    try:
        with _lock, _connect() as c:
            rows = c.execute(f"SELECT data FROM {table} ORDER BY {created_col} DESC LIMIT ?", (limit,)).fetchall()
    except (sqlite3.Error, OSError) as exc:
        print(f"[persist] {table} nicht geladen: {type(exc).__name__}")
        return []
    out = []
    for (raw,) in rows:
        try:
            out.append(json.loads(raw))
        except ValueError:
            continue
    return list(reversed(out))


def load_jobs(limit: int = 50) -> list[dict]:
    return _load("jobs", limit)


def load_approvals(limit: int = 100) -> list[dict]:
    return _load("approvals", limit)


_restored = False


def restore(force: bool = False) -> dict:
    """Stand nach einem Start wiederherstellen (einmal pro Prozess; force fuer Tests)."""
    global _restored
    if _restored and not force:
        return {"jobs": 0, "freigaben": 0}
    _restored = True
    from . import approvals, jobs

    from . import policy

    return {"jobs": jobs.restore(load_jobs()), "freigaben": approvals.restore(load_approvals()),
            "unklare_aktionen": policy.restore()}
