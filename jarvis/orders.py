"""Aufträge (BusinessOrder): fachliche Unternehmens-/Kundenaufträge für das Kanban der Kommandozentrale.

Abgrenzung: Ein Auftrag ist NICHT der technische Job (jobs.py) und NICHT ein Graph-Lauf (graph.py). Jobs und Läufe
werden nur verknüpft (Felder jobs/runs). Ein Verschieben im Kanban ändert nur den Datensatz und startet nie selbst
einen Agenten- oder Werkzeugauftrag.

Garantien:
- Jede Änderung ist versioniert (optimistische Sperre über expected_version -> Konflikt statt Überschreiben),
  idempotent (gleiche request_id liefert dasselbe Ergebnis, ohne doppelt zu ändern) und auditiert
  (order_events mit vorher/nachher, Quelle, Zeit). Eine Änderung läuft in einer Transaktion: Fehler -> nichts geändert.
- Ablage in der vorhandenen SQLite/WAL-Datei (memory.sqlite3), eigene Tabellen.
"""

from __future__ import annotations

import datetime
import json
import re
import time
import uuid

from . import persist

COLUMNS = [("eingang", "Eingang"), ("klaerung", "In Klärung"), ("geplant", "Geplant"),
           ("in_arbeit", "In Arbeit"), ("freigabe", "Warten auf Freigabe"), ("erledigt", "Erledigt")]
STATUSES = [c[0] for c in COLUMNS]
PRIORITIES = ["niedrig", "normal", "hoch", "kritisch"]
EDITABLE = {"titel", "kunde", "projekt", "prioritaet", "verantwortlich", "frist", "status", "position",
            "checkliste", "abhaengigkeiten", "quelle", "notiz"}
_ID = re.compile(r"^[A-Za-z0-9_-]{4,64}$")
_ready: set[str] = set()


class OrderError(ValueError):
    """Ungültige Eingabe (-> 400)."""


class Conflict(Exception):
    """Version passt nicht: jemand anderes hat den Auftrag inzwischen geändert (-> 409)."""

    def __init__(self, current: dict):
        super().__init__("Der Auftrag wurde inzwischen geändert.")
        self.current = current


class NotFound(KeyError):
    pass


def _db():
    cm = persist._connect()
    c = cm.__enter__()
    path = str(persist.db_path())
    if path not in _ready:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS orders (
              id TEXT PRIMARY KEY, version INTEGER NOT NULL, status TEXT NOT NULL, position REAL NOT NULL,
              created REAL NOT NULL, updated REAL NOT NULL, data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS orders_status ON orders(status, position);
            CREATE TABLE IF NOT EXISTS order_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT NOT NULL, request_id TEXT UNIQUE,
              action TEXT NOT NULL, before TEXT, after TEXT, source TEXT NOT NULL, at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS order_events_order ON order_events(order_id, at);
        """)
        _ready.add(path)
    return cm, c


def _clean(changes: dict, partial: bool) -> dict:
    out = {}
    for key, value in (changes or {}).items():
        if key not in EDITABLE:
            raise OrderError(f"Feld '{key}' ist nicht änderbar.")
        if key in ("titel", "kunde", "projekt", "verantwortlich", "quelle", "notiz"):
            value = str(value or "").strip()
            limit = 200 if key == "titel" else 2000 if key == "notiz" else 120
            if len(value) > limit:
                raise OrderError(f"'{key}' ist zu lang (höchstens {limit} Zeichen).")
        elif key == "prioritaet":
            if value not in PRIORITIES:
                raise OrderError("Priorität muss niedrig, normal, hoch oder kritisch sein.")
        elif key == "status":
            if value not in STATUSES:
                raise OrderError(f"Unbekannter Status '{value}'.")
        elif key == "position":
            try:
                value = float(value)
            except (TypeError, ValueError) as exc:
                raise OrderError("Position muss eine Zahl sein.") from exc
        elif key == "frist":
            value = str(value or "").strip()
            if value:
                try:
                    datetime.date.fromisoformat(value[:10])
                except ValueError as exc:
                    raise OrderError("Frist als JJJJ-MM-TT angeben.") from exc
                value = value[:10]
        elif key == "checkliste":
            if not isinstance(value, list) or len(value) > 50:
                raise OrderError("Checkliste muss eine Liste (höchstens 50 Punkte) sein.")
            value = [{"text": str(i.get("text", ""))[:200], "erledigt": bool(i.get("erledigt"))}
                     for i in value if isinstance(i, dict) and str(i.get("text", "")).strip()]
        elif key == "abhaengigkeiten":
            if not isinstance(value, list) or not all(isinstance(x, str) and _ID.match(x) for x in value):
                raise OrderError("Abhängigkeiten müssen Auftrags-IDs sein.")
            value = sorted(set(value))[:20]
        out[key] = value
    if not partial and not out.get("titel"):
        raise OrderError("Ein Auftrag braucht einen Titel.")
    return out


def _row(row) -> dict:
    data = json.loads(row["data"])
    data.update({"id": row["id"], "version": row["version"], "status": row["status"], "position": row["position"],
                 "erstellt": row["created"], "geaendert": row["updated"]})
    return data


def _get(c, order_id: str) -> dict:
    c.row_factory = __import__("sqlite3").Row
    row = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if row is None:
        raise NotFound(order_id)
    return _row(row)


def _replay(c, request_id: str | None) -> dict | None:
    if not request_id:
        return None
    row = c.execute("SELECT after FROM order_events WHERE request_id=?", (request_id,)).fetchone()
    return json.loads(row[0]) if row and row[0] else None


def _store(c, order: dict) -> None:
    data = {k: v for k, v in order.items() if k not in ("id", "version", "status", "position", "erstellt", "geaendert")}
    c.execute("INSERT INTO orders (id, version, status, position, created, updated, data) VALUES (?,?,?,?,?,?,?) "
              "ON CONFLICT(id) DO UPDATE SET version=excluded.version, status=excluded.status, "
              "position=excluded.position, updated=excluded.updated, data=excluded.data",
              (order["id"], order["version"], order["status"], order["position"], order["erstellt"],
               order["geaendert"], json.dumps(data, ensure_ascii=False)))


def _audit(c, order_id, request_id, action, before, after, source) -> None:
    c.execute("INSERT INTO order_events (order_id, request_id, action, before, after, source, at) VALUES (?,?,?,?,?,?,?)",
              (order_id, request_id or None, action, json.dumps(before, ensure_ascii=False) if before else None,
               json.dumps(after, ensure_ascii=False), str(source)[:40], time.time()))


def _check_request_id(request_id: str | None) -> None:
    if request_id is not None and not _ID.match(str(request_id)):
        raise OrderError("Ungültige request_id.")


def create(fields: dict, *, request_id: str | None = None, source: str = "nutzer") -> dict:
    _check_request_id(request_id)
    clean = _clean(fields, partial=False)
    cm, c = _db()
    try:
        c.execute("BEGIN IMMEDIATE")
        replay = _replay(c, request_id)
        if replay is not None:
            c.execute("ROLLBACK")
            return replay
        now = time.time()
        status = clean.pop("status", "eingang")
        top = c.execute("SELECT MAX(position) FROM orders WHERE status=?", (status,)).fetchone()[0]
        order = {"id": "A-" + uuid.uuid4().hex[:10], "version": 1, "status": status,
                 "position": clean.pop("position", (top or 0) + 1000), "erstellt": now, "geaendert": now,
                 "titel": "", "kunde": "", "projekt": "", "prioritaet": "normal", "verantwortlich": "",
                 "frist": "", "checkliste": [], "abhaengigkeiten": [], "quelle": "", "notiz": "",
                 "jobs": [], "runs": [], **clean}
        _store(c, order)
        _audit(c, order["id"], request_id, "angelegt", None, order, source)
        c.execute("COMMIT")
        return order
    except BaseException:
        if c.in_transaction:
            c.execute("ROLLBACK")
        raise
    finally:
        cm.__exit__(None, None, None)


def update(order_id: str, changes: dict, *, expected_version: int | None, request_id: str | None = None,
           source: str = "nutzer") -> dict:
    """Ändert Felder (auch Status/Position = Verschieben). expected_version None nur für interne Aufrufer."""
    _check_request_id(request_id)
    if not _ID.match(str(order_id)):
        raise OrderError("Ungültige Auftrags-ID.")
    clean = _clean(changes, partial=True)
    if not clean:
        raise OrderError("Keine Änderung angegeben.")
    cm, c = _db()
    try:
        c.execute("BEGIN IMMEDIATE")
        replay = _replay(c, request_id)
        if replay is not None:
            c.execute("ROLLBACK")
            return replay
        before = _get(c, order_id)
        if expected_version is not None and int(expected_version) != before["version"]:
            c.execute("ROLLBACK")
            raise Conflict(before)
        if order_id in clean.get("abhaengigkeiten", []):
            raise OrderError("Ein Auftrag kann nicht von sich selbst abhängen.")
        after = {**before, **clean, "version": before["version"] + 1, "geaendert": time.time()}
        _store(c, after)
        action = "verschoben" if set(clean) <= {"status", "position"} else "geaendert"
        _audit(c, order_id, request_id, action, before, after, source)
        c.execute("COMMIT")
        return after
    except BaseException:
        if c.in_transaction:
            c.execute("ROLLBACK")
        raise
    finally:
        cm.__exit__(None, None, None)


def link(order_id: str, *, job_id: str | None = None, run_id: str | None = None, source: str = "runtime") -> dict:
    """Technischen Job oder Graph-Lauf mit einem Auftrag verknüpfen (gleichsetzen ist ausgeschlossen)."""
    cm, c = _db()
    try:
        c.execute("BEGIN IMMEDIATE")
        before = _get(c, order_id)
        after = dict(before)
        if job_id and job_id not in after["jobs"]:
            after["jobs"] = [*after["jobs"], job_id][-50:]
        if run_id and run_id not in after["runs"]:
            after["runs"] = [*after["runs"], run_id][-50:]
        after["version"] += 1
        after["geaendert"] = time.time()
        _store(c, after)
        _audit(c, order_id, None, "verknuepft", before, after, source)
        c.execute("COMMIT")
        return after
    except BaseException:
        if c.in_transaction:
            c.execute("ROLLBACK")
        raise
    finally:
        cm.__exit__(None, None, None)


def listing(include_done: bool = True) -> dict:
    cm, c = _db()
    try:
        c.row_factory = __import__("sqlite3").Row
        rows = c.execute("SELECT * FROM orders ORDER BY status, position").fetchall()
    finally:
        cm.__exit__(None, None, None)
    orders = [_row(r) for r in rows]
    if not include_done:
        orders = [o for o in orders if o["status"] != "erledigt"]
    return {"spalten": [{"id": k, "titel": t} for k, t in COLUMNS], "auftraege": orders}


def history(order_id: str, limit: int = 50) -> list[dict]:
    cm, c = _db()
    try:
        rows = c.execute("SELECT action, source, at, request_id FROM order_events WHERE order_id=? ORDER BY id DESC "
                         "LIMIT ?", (order_id, max(1, min(200, limit)))).fetchall()
    finally:
        cm.__exit__(None, None, None)
    return [{"aktion": a, "quelle": s, "zeit": t, "request_id": r} for a, s, t, r in rows]


# --- Werkzeuge für Jarvis (lokal, ohne Außenwirkung) --------------------------------------------------

def _tools():
    from . import faehigkeiten
    from .tools import Tool, ToolFailure, integer, string

    def auftraege_liste(status: str = "") -> str:
        data = listing(include_done=status == "erledigt")
        items = [o for o in data["auftraege"] if not status or o["status"] == status]
        if not items:
            return "Keine Aufträge" + (f" mit Status {status}." if status else ".")
        return "\n".join(f"{o['id']} | {o['titel']} | {o['status']} | Prio {o['prioritaet']} | Kunde {o['kunde'] or '-'}"
                         f" | Frist {o['frist'] or '-'} | v{o['version']}" for o in items[:60])

    def auftrag_aktualisieren(id: str, version: int, status: str = "", prioritaet: str = "", frist: str = "",
                              notiz: str = "") -> str:
        changes = {k: v for k, v in {"status": status, "prioritaet": prioritaet, "frist": frist, "notiz": notiz}.items() if v}
        try:
            o = update(id, changes, expected_version=version, source="jarvis")
        except Conflict as exc:
            raise ToolFailure(f"Konflikt: Auftrag ist inzwischen bei Version {exc.current['version']}. Neu lesen.") from exc
        except (OrderError, NotFound) as exc:
            raise ToolFailure(f"Nicht geändert: {exc}") from exc
        return f"Auftrag {o['id']} aktualisiert (v{o['version']}, {o['status']})."

    return [
        Tool("auftraege_liste", "Listet die Aufträge aus der Kommandozentrale (Kanban), optional nach Status.",
             {"status": string("eingang, klaerung, geplant, in_arbeit, freigabe oder erledigt (optional)")},
             auftraege_liste),
        # Anlegen ist eine Laufzeitfähigkeit (faehigkeiten.py): eigener Job, Status, Abbruch, im Command Center sichtbar.
        faehigkeiten.tool("auftrag_anlegen"),
        Tool("auftrag_aktualisieren", "Ändert Status, Priorität, Frist oder Notiz eines Auftrags (Version aus auftraege_liste).",
             {"id": string("Auftrags-ID"), "version": integer("aktuelle Version"), "status": string("neuer Status (optional)"),
              "prioritaet": string("optional"), "frist": string("JJJJ-MM-TT (optional)"), "notiz": string("optional")},
             auftrag_aktualisieren, ["id", "version"]),
    ]


def tools():
    from .tools import Tool

    def ablaeufe_status() -> str:
        from . import coach, scheduler  # noqa: F401
        return scheduler.status_text()

    return [*_tools(), Tool("ablaeufe_status",
                            "Zeigt die geplanten internen Ablaeufe (Coach, Fristwarnung, Mail zu Auftrag, Lead-Follow-up, "
                            "Terminvorbereitung): an/aus und Status des letzten Laufs.", {}, ablaeufe_status)]
