"""Content-Workflow: Idee -> Recherche -> Skript -> Produktion -> Review -> Freigegeben -> Geplant -> Veröffentlicht -> Auswertung.

Nur lokaler Speicher. Jarvis veröffentlicht nichts: Jarvis darf einen Eintrag höchstens bis „Review“ voranbringen;
„Freigegeben“ und alles danach setzt der Nutzer selbst im Dashboard. Kennzahlen (Wiedergabezeit, Abschlussrate, Klicks,
Kommentare, Follower, Leads, Umsatz) sind vom Nutzer erfasste Werte mit Zeitpunkt – es gibt keine automatische
Plattformanbindung und keine erfundenen Werte.
"""

from __future__ import annotations

import json
import re
import time
import uuid

from . import persist

STATUS = [("idee", "Idee"), ("recherche", "Recherche/Briefing"), ("skript", "Skript"), ("produktion", "Produktion"),
          ("review", "Review"), ("freigegeben", "Freigegeben"), ("geplant", "Geplant"),
          ("veroeffentlicht", "Veröffentlicht"), ("auswertung", "Auswertung")]
IDS = [s[0] for s in STATUS]
JARVIS_MAX = IDS.index("review")          # darüber nur der Nutzer
PLATTFORMEN = ["tiktok", "instagram", "youtube_shorts", "youtube"]
KRITERIEN = ["klarheit", "relevanz", "neugier", "belegbarkeit"]
METRIKEN = ["wiedergabezeit_s", "abschlussrate_pct", "views", "klicks", "kommentare", "follower", "leads", "umsatz_eur"]
_ID = re.compile(r"^K-[0-9a-f]{8}$")
_ready: set[str] = set()


class ContentError(ValueError):
    pass


def _db():
    cm = persist._connect()
    c = cm.__enter__()
    path = str(persist.db_path())
    if path not in _ready:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS content_items (
              id TEXT PRIMARY KEY, version INTEGER NOT NULL, status TEXT NOT NULL, created REAL NOT NULL,
              updated REAL NOT NULL, data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS content_status ON content_items(status, updated DESC);
        """)
        _ready.add(path)
    return cm, c


def _s(value, limit, name):
    value = str(value or "").strip()
    if len(value) > limit:
        raise ContentError(f"'{name}' ist zu lang (höchstens {limit} Zeichen).")
    return value


def _hooks(value) -> list[dict]:
    if not isinstance(value, list) or len(value) > 20:
        raise ContentError("Hooks müssen eine Liste (höchstens 20) sein.")
    out = []
    for h in value:
        if isinstance(h, str):
            h = {"text": h}
        if not isinstance(h, dict) or not str(h.get("text", "")).strip():
            raise ContentError("Jeder Hook braucht einen Text.")
        scores = {}
        for k in KRITERIEN:
            if h.get(k) not in (None, ""):
                try:
                    v = int(h[k])
                except (TypeError, ValueError) as exc:
                    raise ContentError(f"Bewertung '{k}' muss 1–5 sein.") from exc
                if not 1 <= v <= 5:
                    raise ContentError(f"Bewertung '{k}' muss 1–5 sein.")
                scores[k] = v
        out.append({"text": _s(h["text"], 300, "hook"), "belegt_durch": _s(h.get("belegt_durch"), 300, "belegt_durch"),
                    **scores, "summe": sum(scores.values()) if len(scores) == len(KRITERIEN) else None})
    return out


def _clean(fields: dict) -> dict:
    out: dict = {}
    for k, lim in (("titel", 200), ("thema", 500), ("ziel", 300), ("zielgruppe", 300), ("skript", 8000),
                   ("briefing", 4000), ("notiz", 2000), ("ki_hinweis", 300)):
        if k in fields:
            out[k] = _s(fields[k], lim, k)
    if "plattformen" in fields:
        pl = fields["plattformen"]
        if isinstance(pl, str):
            pl = [x.strip() for x in pl.split(",") if x.strip()]
        if not isinstance(pl, list) or any(p not in PLATTFORMEN for p in pl):
            raise ContentError("Plattformen: " + ", ".join(PLATTFORMEN))
        out["plattformen"] = pl
    if "hooks" in fields:
        out["hooks"] = _hooks(fields["hooks"])
    if "quellen" in fields:
        q = fields["quellen"]
        if isinstance(q, str):
            q = [x.strip() for x in q.splitlines() if x.strip()]
        if not isinstance(q, list) or len(q) > 30:
            raise ContentError("Quellen müssen eine Liste (höchstens 30) sein.")
        out["quellen"] = [_s(x, 300, "quelle") for x in q]
    return out


def speichern(fields: dict, item_id: str | None = None) -> dict:
    clean = _clean(fields)
    if item_id is not None and not _ID.match(item_id):
        raise ContentError("Ungültige Content-ID.")
    cm, c = _db()
    try:
        c.execute("BEGIN IMMEDIATE")
        now = time.time()
        if item_id:
            row = c.execute("SELECT version, status, created, data FROM content_items WHERE id=?", (item_id,)).fetchone()
            if row is None:
                raise ContentError("Content-Eintrag nicht gefunden.")
            version, status, created, data = row[0] + 1, row[1], row[2], {**json.loads(row[3]), **clean}
        else:
            if not clean.get("titel"):
                raise ContentError("Ein Content-Eintrag braucht einen Titel.")
            item_id, version, status, created = "K-" + uuid.uuid4().hex[:8], 1, "idee", now
            data = {"titel": "", "thema": "", "ziel": "", "zielgruppe": "", "skript": "", "briefing": "", "notiz": "",
                    "ki_hinweis": "", "plattformen": [], "hooks": [], "quellen": [], "ergebnisse": [], **clean}
        c.execute("INSERT INTO content_items (id, version, status, created, updated, data) VALUES (?,?,?,?,?,?) "
                  "ON CONFLICT(id) DO UPDATE SET version=excluded.version, updated=excluded.updated, data=excluded.data",
                  (item_id, version, status, created, now, json.dumps(data, ensure_ascii=False)))
        c.execute("COMMIT")
        return {"id": item_id, "version": version, "status": status, "erstellt": created, "geaendert": now, **data}
    except BaseException:
        if c.in_transaction:
            c.execute("ROLLBACK")
        raise
    finally:
        cm.__exit__(None, None, None)


def status_setzen(item_id: str, status: str, von: str) -> dict:
    """von='jarvis' darf nur bis Review; von='nutzer' (Dashboard) darf alles."""
    if status not in IDS:
        raise ContentError("Unbekannter Status.")
    if von != "nutzer" and IDS.index(status) > JARVIS_MAX:
        raise ContentError("Ab „Freigegeben“ setzt nur der Nutzer den Status (im Dashboard). Jarvis veröffentlicht nichts.")
    cm, c = _db()
    try:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT version, created, data FROM content_items WHERE id=?", (item_id,)).fetchone()
        if row is None:
            raise ContentError("Content-Eintrag nicht gefunden.")
        now = time.time()
        c.execute("UPDATE content_items SET status=?, version=?, updated=? WHERE id=?", (status, row[0] + 1, now, item_id))
        c.execute("COMMIT")
        return {"id": item_id, "version": row[0] + 1, "status": status, "erstellt": row[1], "geaendert": now, **json.loads(row[2])}
    except BaseException:
        if c.in_transaction:
            c.execute("ROLLBACK")
        raise
    finally:
        cm.__exit__(None, None, None)


def ergebnis_erfassen(item_id: str, plattform: str, werte: dict) -> dict:
    """Vom Nutzer genannte Kennzahlen eines veröffentlichten Beitrags speichern (mit Zeitpunkt, Quelle 'manuell')."""
    if plattform not in PLATTFORMEN:
        raise ContentError("Plattform: " + ", ".join(PLATTFORMEN))
    clean = {}
    for k in METRIKEN:
        if werte.get(k) not in (None, ""):
            try:
                clean[k] = float(werte[k])
            except (TypeError, ValueError) as exc:
                raise ContentError(f"'{k}' muss eine Zahl sein.") from exc
    if not clean:
        raise ContentError("Mindestens eine Kennzahl nennen: " + ", ".join(METRIKEN))
    cm, c = _db()
    try:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT version, status, created, data FROM content_items WHERE id=?", (item_id,)).fetchone()
        if row is None:
            raise ContentError("Content-Eintrag nicht gefunden.")
        data = json.loads(row[3])
        data.setdefault("ergebnisse", []).append({"plattform": plattform, "erfasst": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                                  "quelle": "manuell vom Nutzer genannt", **clean})
        now = time.time()
        c.execute("UPDATE content_items SET version=?, updated=?, data=? WHERE id=?",
                  (row[0] + 1, now, json.dumps(data, ensure_ascii=False), item_id))
        c.execute("COMMIT")
        return {"id": item_id, "version": row[0] + 1, "status": row[1], "erstellt": row[2], "geaendert": now, **data}
    except BaseException:
        if c.in_transaction:
            c.execute("ROLLBACK")
        raise
    finally:
        cm.__exit__(None, None, None)


def liste(status: str | None = None, limit: int = 100) -> list[dict]:
    cm, c = _db()
    try:
        if status:
            rows = c.execute("SELECT id, version, status, created, updated, data FROM content_items WHERE status=? "
                             "ORDER BY updated DESC LIMIT ?", (status, limit)).fetchall()
        else:
            rows = c.execute("SELECT id, version, status, created, updated, data FROM content_items "
                             "ORDER BY updated DESC LIMIT ?", (limit,)).fetchall()
    finally:
        cm.__exit__(None, None, None)
    return [{"id": r[0], "version": r[1], "status": r[2], "erstellt": r[3], "geaendert": r[4], **json.loads(r[5])} for r in rows]


def uebersicht() -> dict:
    items = liste()
    return {"spalten": [{"id": i, "titel": t} for i, t in STATUS], "eintraege": items, "plattformen": PLATTFORMEN,
            "quelle": "lokal gespeichert; keine Plattform-Anbindung, Kennzahlen nur manuell erfasst"}
