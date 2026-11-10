"""Anruf-Copilot: Projekte, Anruf-Ergebnisse, Kennzahlen und Lead-Karte (nur lokale Daten, SQLite).

Ein Projekt ist ein Datensatz (CRM-Typ, Zielgruppe, Leitfaden, Regeln) – kein Projektwissen steckt im Code.
Jeder Anruf endet im selben Ergebnisformat, unabhängig vom CRM. CRM-Schreibzugriffe passieren NICHT hier, sondern über
die vorhandenen, freigabepflichtigen Werkzeuge (z. B. hubspot_notiz_erstellen); ``crm_status`` hält nur fest, wie weit
der CRM-Eintrag ist (offen | vorschau | geschrieben | nicht_noetig).

Garantien (wie orders.py): Anruf-Erfassung ist idempotent (gleiche request_id -> derselbe Anruf, nichts doppelt),
läuft in einer Transaktion und nennt nur Zahlen, die tatsächlich gespeichert sind.
"""

from __future__ import annotations

import datetime
import json
import re
import time
import uuid

from . import persist

CRMS = ["hubspot", "gohighlevel", "close", "sonstiges", "keins"]
ERGEBNISSE = {
    "nicht_erreicht": "Nicht erreicht", "falsche_nummer": "Falsche Nummer", "kein_interesse": "Kein Interesse",
    "rueckruf": "Rückruf vereinbart", "interesse": "Interesse, nächster Schritt offen",
    "termin": "Termin vereinbart", "abschluss": "Abschluss",
}
TELEFON_STATUS = ["geschaeftlich", "privat", "unklar"]
CRM_STATUS = ["offen", "vorschau", "geschrieben", "nicht_noetig"]
_ID = re.compile(r"^[A-Za-z0-9_-]{4,64}$")
_ready: set[str] = set()


class AnrufError(ValueError):
    """Ungültige Eingabe."""


def _db():
    cm = persist._connect()
    c = cm.__enter__()
    path = str(persist.db_path())
    if path not in _ready:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS projekte (
              id TEXT PRIMARY KEY, version INTEGER NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
              data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS anrufe (
              id TEXT PRIMARY KEY, request_id TEXT UNIQUE, projekt_id TEXT NOT NULL, created REAL NOT NULL,
              data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS anrufe_zeit ON anrufe(created DESC);
            CREATE INDEX IF NOT EXISTS anrufe_projekt ON anrufe(projekt_id, created DESC);
        """)
        _ready.add(path)
    return cm, c


def _text(value, limit: int, name: str) -> str:
    value = str(value or "").strip()
    if len(value) > limit:
        raise AnrufError(f"'{name}' ist zu lang (höchstens {limit} Zeichen).")
    return value


# --- Projekte -----------------------------------------------------------------------------------------------------

def projekt_speichern(fields: dict, projekt_id: str | None = None) -> dict:
    """Legt ein Projekt an oder ändert es (Teiländerung über projekt_id)."""
    clean: dict = {}
    for key, limit in (("name", 120), ("zielgruppe", 1000), ("leitfaden", 4000), ("regeln", 2000), ("notiz", 2000)):
        if key in fields:
            clean[key] = _text(fields[key], limit, key)
    if "crm" in fields:
        if fields["crm"] not in CRMS:
            raise AnrufError("CRM muss hubspot, gohighlevel, close, sonstiges oder keins sein.")
        clean["crm"] = fields["crm"]
    if "aktiv" in fields:
        clean["aktiv"] = bool(fields["aktiv"])
    if projekt_id is not None and not _ID.match(projekt_id):
        raise AnrufError("Ungültige Projekt-ID.")
    cm, c = _db()
    try:
        c.execute("BEGIN IMMEDIATE")
        now = time.time()
        if projekt_id:
            row = c.execute("SELECT version, created, data FROM projekte WHERE id=?", (projekt_id,)).fetchone()
            if row is None:
                raise AnrufError("Projekt nicht gefunden.")
            data = {**json.loads(row[2]), **clean}
            version, created = row[0] + 1, row[1]
        else:
            if not clean.get("name"):
                raise AnrufError("Ein Projekt braucht einen Namen.")
            projekt_id, version, created = "P-" + uuid.uuid4().hex[:8], 1, now
            data = {"name": "", "crm": "keins", "zielgruppe": "", "leitfaden": "", "regeln": "", "notiz": "",
                    "aktiv": True, **clean}
        c.execute("INSERT INTO projekte (id, version, created, updated, data) VALUES (?,?,?,?,?) "
                  "ON CONFLICT(id) DO UPDATE SET version=excluded.version, updated=excluded.updated, data=excluded.data",
                  (projekt_id, version, created, now, json.dumps(data, ensure_ascii=False)))
        c.execute("COMMIT")
        return {"id": projekt_id, "version": version, **data}
    except BaseException:
        if c.in_transaction:
            c.execute("ROLLBACK")
        raise
    finally:
        cm.__exit__(None, None, None)


def projekte_liste(nur_aktive: bool = True) -> list[dict]:
    cm, c = _db()
    try:
        rows = c.execute("SELECT id, version, data FROM projekte ORDER BY updated DESC").fetchall()
    finally:
        cm.__exit__(None, None, None)
    out = [{"id": r[0], "version": r[1], **json.loads(r[2])} for r in rows]
    return [p for p in out if p.get("aktiv", True)] if nur_aktive else out


def projekt_finden(text: str | None) -> str:
    """Projekt-ID aus ID oder Name. Ohne Angabe: das einzige aktive Projekt. Mehrdeutig oder unbekannt -> Fehler."""
    aktive = projekte_liste(nur_aktive=True)
    t = str(text or "").strip().lower()
    if not t:
        if len(aktive) == 1:
            return aktive[0]["id"]
        raise AnrufError("Projekt angeben (projekt_liste zeigt: " + ", ".join(p["name"] for p in aktive) + ")."
                         if aktive else "Noch kein Projekt vorhanden. Erst projekt_speichern.")
    for p in aktive:
        if p["id"].lower() == t:
            return p["id"]
    treffer = [p for p in aktive if p["name"].lower() == t] or [p for p in aktive if t in p["name"].lower()]
    if len(treffer) == 1:
        return treffer[0]["id"]
    raise AnrufError(f"Projekt '{text}' " + ("ist mehrdeutig: " + ", ".join(p["name"] for p in treffer) if treffer
                                              else "nicht gefunden. Vorhanden: " + (", ".join(p["name"] for p in aktive) or "keins")))


def _projekt(c, projekt_id: str) -> dict:
    row = c.execute("SELECT data FROM projekte WHERE id=?", (projekt_id,)).fetchone()
    if row is None:
        raise AnrufError("Projekt nicht gefunden. Erst mit projekt_speichern anlegen (projekt_liste zeigt die vorhandenen).")
    return json.loads(row[0])


# --- Anrufe -------------------------------------------------------------------------------------------------------

def _clean_call(fields: dict) -> dict:
    ergebnis = fields.get("ergebnis")
    if ergebnis not in ERGEBNISSE:
        raise AnrufError("Ergebnis muss eines von: " + ", ".join(ERGEBNISSE) + " sein.")
    telefon = fields.get("telefon_status") or "unklar"
    if telefon not in TELEFON_STATUS:
        raise AnrufError("telefon_status muss geschaeftlich, privat oder unklar sein.")
    frist = str(fields.get("frist") or "").strip()
    if frist:
        try:
            datetime.date.fromisoformat(frist[:10])
        except ValueError as exc:
            raise AnrufError("Frist als JJJJ-MM-TT angeben.") from exc
        frist = frist[:10]
    crm = fields.get("crm_status") or "offen"
    if crm not in CRM_STATUS:
        raise AnrufError("crm_status muss offen, vorschau, geschrieben oder nicht_noetig sein.")
    dauer = fields.get("dauer_s")
    if dauer not in (None, ""):
        try:
            dauer = max(0, min(int(dauer), 36000))
        except (TypeError, ValueError) as exc:
            raise AnrufError("dauer_s muss eine Zahl (Sekunden) sein.") from exc
    else:
        dauer = None
    return {
        "lead_name": _text(fields.get("lead_name"), 120, "lead_name"),
        "firma": _text(fields.get("firma"), 160, "firma"),
        "rolle": _text(fields.get("rolle"), 120, "rolle"),
        "telefon_status": telefon,
        "kontakt_quelle": _text(fields.get("kontakt_quelle"), 200, "kontakt_quelle"),
        "ergebnis": ergebnis,
        "notiz": _text(fields.get("notiz"), 2000, "notiz"),
        "naechster_schritt": _text(fields.get("naechster_schritt"), 300, "naechster_schritt"),
        "frist": frist, "crm_status": crm, "dauer_s": dauer, "auftrag_id": "",
    }


def anruf_erfassen(fields: dict, projekt_id: str, request_id: str | None = None) -> dict:
    """Speichert ein Anruf-Ergebnis. Mit nächstem Schritt entsteht ein Auftrag im Kanban (Eingang), nie ein CRM-Eintrag."""
    if request_id is not None and not _ID.match(str(request_id)):
        raise AnrufError("Ungültige request_id.")
    clean = _clean_call(fields)
    if not (clean["lead_name"] or clean["firma"]):
        raise AnrufError("Mindestens Name oder Firma des Leads angeben.")
    cm, c = _db()
    try:
        c.execute("BEGIN IMMEDIATE")
        projekt = _projekt(c, projekt_id)
        if request_id:
            row = c.execute("SELECT id, data, created FROM anrufe WHERE request_id=?", (request_id,)).fetchone()
            if row:
                c.execute("ROLLBACK")
                return {"id": row[0], "projekt_id": projekt_id, "erstellt": row[2], "wiederholt": True, **json.loads(row[1])}
        ident, now = "C-" + uuid.uuid4().hex[:10], time.time()
        c.execute("INSERT INTO anrufe (id, request_id, projekt_id, created, data) VALUES (?,?,?,?,?)",
                  (ident, request_id or None, projekt_id, now, json.dumps(clean, ensure_ascii=False)))
        c.execute("COMMIT")
    except BaseException:
        if c.in_transaction:
            c.execute("ROLLBACK")
        raise
    finally:
        cm.__exit__(None, None, None)
    call = {"id": ident, "projekt_id": projekt_id, "erstellt": now, "wiederholt": False, **clean}
    if clean["naechster_schritt"]:
        from . import orders
        try:
            lead = clean["firma"] or clean["lead_name"]
            order = orders.create({"titel": f"{clean['naechster_schritt']} – {lead}"[:200], "kunde": projekt["name"][:120],
                                   "frist": clean["frist"], "quelle": f"anruf:{ident}", "prioritaet": "normal",
                                   "notiz": clean["notiz"][:2000]},
                                  request_id="anruf-" + ident, source="anruf")
            call["auftrag_id"] = order["id"]
            cm, c = _db()
            try:
                c.execute("UPDATE anrufe SET data=? WHERE id=?",
                          (json.dumps({k: call[k] for k in clean} | {"auftrag_id": order["id"]}, ensure_ascii=False), ident))
            finally:
                cm.__exit__(None, None, None)
        except orders.OrderError as exc:  # Anruf bleibt gespeichert; der Fehler wird sichtbar gemeldet
            call["auftrag_fehler"] = str(exc)
    return call


def crm_status_setzen(anruf_id: str, status: str) -> dict:
    if status not in CRM_STATUS:
        raise AnrufError("crm_status muss offen, vorschau, geschrieben oder nicht_noetig sein.")
    cm, c = _db()
    try:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT projekt_id, created, data FROM anrufe WHERE id=?", (anruf_id,)).fetchone()
        if row is None:
            c.execute("ROLLBACK")
            raise AnrufError("Anruf nicht gefunden.")
        data = json.loads(row[2])
        data["crm_status"] = status
        c.execute("UPDATE anrufe SET data=? WHERE id=?", (json.dumps(data, ensure_ascii=False), anruf_id))
        c.execute("COMMIT")
        return {"id": anruf_id, "projekt_id": row[0], "erstellt": row[1], **data}
    except BaseException:
        if c.in_transaction:
            c.execute("ROLLBACK")
        raise
    finally:
        cm.__exit__(None, None, None)


def anrufe_liste(projekt_id: str | None = None, tage: int = 7, limit: int = 50) -> list[dict]:
    since = time.time() - max(1, min(int(tage), 365)) * 86400
    cm, c = _db()
    try:
        if projekt_id:
            rows = c.execute("SELECT id, projekt_id, created, data FROM anrufe WHERE created>=? AND projekt_id=? "
                             "ORDER BY created DESC LIMIT ?", (since, projekt_id, limit)).fetchall()
        else:
            rows = c.execute("SELECT id, projekt_id, created, data FROM anrufe WHERE created>=? "
                             "ORDER BY created DESC LIMIT ?", (since, limit)).fetchall()
    finally:
        cm.__exit__(None, None, None)
    return [{"id": r[0], "projekt_id": r[1], "erstellt": r[2], **json.loads(r[3])} for r in rows]


def kennzahlen(tage: int = 7, projekt_id: str | None = None) -> dict:
    """Gemessene Zahlen aus gespeicherten Anrufen. Termine pro 100 nur mit mindestens einem Anruf, sonst None."""
    calls = anrufe_liste(projekt_id, tage, limit=100000)
    total = len(calls)
    nach: dict[str, int] = {k: 0 for k in ERGEBNISSE}
    for call in calls:
        nach[call["ergebnis"]] = nach.get(call["ergebnis"], 0) + 1
    termine = nach["termin"] + nach["abschluss"]
    heute = datetime.date.today().isoformat()
    offene = [c for c in calls if c["naechster_schritt"] and c["frist"] and c["frist"] <= heute]
    dauern = [c["dauer_s"] for c in calls if c.get("dauer_s")]
    return {
        "zeitraum_tage": tage, "projekt_id": projekt_id, "gespraeche": total, "nach_ergebnis": nach,
        "termine": termine, "termine_pro_100": round(termine * 100 / total, 1) if total else None,
        "crm_offen": sum(1 for c in calls if c["crm_status"] == "offen" and c["ergebnis"] not in ("nicht_erreicht", "falsche_nummer")),
        "followups_faellig": len(offene), "mit_naechstem_schritt": sum(1 for c in calls if c["naechster_schritt"]),
        "mittlere_dauer_s": round(sum(dauern) / len(dauern)) if dauern else None,
        "quelle": "gespeicherte Anrufe in memory.sqlite3 (nur von Jarvis erfasste Gespräche)",
        "stand": datetime.datetime.now().isoformat(timespec="seconds"),
    }


def lead_karte(projekt_id: str, name: str = "", firma: str = "") -> dict:
    """Mini-Karte vor dem Anruf: Projekt-Leitfaden, bisherige Anrufe zum Lead, offene Follow-ups, Telefon-Hinweise.
    Aus lokalen Daten; CRM-Stammdaten holt Jarvis bei Bedarf über das CRM-Werkzeug (die Karte nennt, ob eines nötig ist)."""
    if not (name.strip() or firma.strip()):
        raise AnrufError("Name oder Firma angeben.")
    cm, c = _db()
    try:
        projekt = _projekt(c, projekt_id)
    finally:
        cm.__exit__(None, None, None)
    n, f = name.strip().lower(), firma.strip().lower()
    frueher = [a for a in anrufe_liste(projekt_id, tage=365, limit=1000)
               if (f and f in a["firma"].lower()) or (n and n in a["lead_name"].lower())]
    heute = datetime.date.today().isoformat()
    offene = [a for a in frueher if a["naechster_schritt"] and (not a["frist"] or a["frist"] <= heute)]
    hinweise = []
    if any(a["telefon_status"] != "geschaeftlich" for a in frueher):
        hinweise.append("Telefonnummer war bisher privat oder unklar: Werbeanruf nur mit geklärter Rechtsgrundlage.")
    if any(a["ergebnis"] == "kein_interesse" for a in frueher):
        hinweise.append("Früher „Kein Interesse“ – Anruf nur mit neuem Anlass.")
    crm_tool = {"hubspot": "hubspot_kontakt_suchen"}.get(projekt.get("crm", ""))
    return {
        "projekt": {"id": projekt_id, "name": projekt.get("name", ""), "crm": projekt.get("crm", "keins"),
                    "zielgruppe": projekt.get("zielgruppe", ""), "leitfaden": projekt.get("leitfaden", ""),
                    "regeln": projekt.get("regeln", "")},
        "lead": {"name": name.strip(), "firma": firma.strip()},
        "bisherige_anrufe": [{"id": a["id"], "wann": datetime.datetime.fromtimestamp(a["erstellt"]).isoformat(timespec="minutes"),
                              "ergebnis": a["ergebnis"], "notiz": a["notiz"], "naechster_schritt": a["naechster_schritt"],
                              "frist": a["frist"]} for a in frueher[:5]],
        "offene_followups": [a["naechster_schritt"] for a in offene],
        "hinweise": hinweise,
        "crm_abfrage": (f"CRM-Stammdaten fehlen noch: {crm_tool} aufrufen." if crm_tool else
                        "Für dieses Projekt ist keine CRM-Abfrage eingerichtet; Karte nur aus lokalen Daten."),
        "quelle": "lokale Anrufhistorie und Projekt-Datensatz; keine Webrecherche",
    }


def uebersicht(tage: int = 7) -> dict:
    """Alles für die Anrufe-Ansicht in einem Aufruf."""
    return {"projekte": projekte_liste(nur_aktive=False), "kennzahlen": kennzahlen(tage),
            "letzte_anrufe": anrufe_liste(None, tage, 30), "ergebnisse": ERGEBNISSE}
