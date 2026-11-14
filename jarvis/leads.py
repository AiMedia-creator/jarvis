"""Cold-Call-CRM: Leads, Aktivitäten und Call-Sessions (nur lokale Daten, dieselbe SQLite-Datei wie Aufträge und Anrufe).

Ein Lead ist ein eigener Datensatz mit Status, Kontakt- und Firmendaten, Notiz, nächstem Schritt und Verlauf.
Jedes erfasste Anruf-Ergebnis schreibt IN EINER TRANSAKTION genau eine Aktivität, den neuen Lead-Stand und genau eine
Zeile in ``anrufe`` (damit die gemessenen Vertriebskennzahlen weiter stimmen). Es entsteht dabei KEIN Kanban-Auftrag
und KEIN CRM-Eintrag beim Kunden; Follow-ups leben am Lead.

Garantien:
- Idempotent: gleiche ``request_id`` -> dieselbe Aktivität bzw. derselbe Lead, nichts doppelt.
- Versioniert: jede Änderung nennt die erwartete Version; sonst ``Konflikt`` mit dem aktuellen Stand (nichts wird still
  überschrieben).
- Duplikate: gleiche (normalisierte) Telefonnummer wird immer abgelehnt (eindeutiger Index). Gleiche Firma + Name ist
  ein Hinweis, der eine ausdrückliche Bestätigung verlangt.
- Jarvis wählt nie selbst. Telefonnummern sind nur Daten.
"""

from __future__ import annotations

import csv
import datetime
import io
import json
import re
import secrets
import threading
import time
import uuid

from . import anrufe

# Statusmodell: erweiterbar. Neue Status hier ergänzen; ``anruf`` ist das Ergebnis für die Anruf-Kennzahlen
# (None = zählt nicht als Anruf), ``offen`` = Lead steht weiter auf der Anrufliste.
STATUS: dict[str, dict] = {
    "neu": {"titel": "Neu", "offen": True, "anruf": None, "ton": "neutral"},
    "nicht_erreicht": {"titel": "Nicht erreicht", "offen": True, "anruf": "nicht_erreicht", "ton": "neutral"},
    "mailbox": {"titel": "Mailbox", "offen": True, "anruf": "nicht_erreicht", "ton": "warn"},
    "follow_up": {"titel": "Follow-up", "offen": True, "anruf": "rueckruf", "ton": "arbeit"},
    "meeting_gebucht": {"titel": "Meeting gebucht", "offen": False, "anruf": "termin", "ton": "ok"},
    "kein_interesse": {"titel": "Kein Interesse", "offen": False, "anruf": "kein_interesse", "ton": "danger"},
}
TELEFON_STATUS = anrufe.TELEFON_STATUS
FELDER = {"firma": 160, "name": 120, "rolle": 120, "telefon": 40, "email": 160, "website": 200, "ort": 120,
          "quelle": 200, "notiz": 4000, "naechster_schritt": 300}
CSV_SPALTEN = ["firma", "name", "rolle", "telefon", "email", "website", "notiz"]
CSV_MAX_BYTES = 1_000_000  # Plan: höchstens 1 MB (dezimal). Gilt gleich in Oberfläche, Java und hier.
CSV_MAX_ZEILEN = 2000
_ID = re.compile(r"^[A-Za-z0-9_-]{4,64}$")
_RECHTSFORM = re.compile(r"\b(gmbh\s*&\s*co\.?\s*kg|gmbh|mbh|ag|ug|kg|ohg|gbr|se|e\.?\s?k\.?|ltd|inc)\b\.?", re.I)
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$")
_ready: set[str] = set()


class LeadError(ValueError):
    """Ungültige Eingabe."""


class NichtGefunden(LeadError):
    pass


class Konflikt(LeadError):
    """Der Lead wurde inzwischen geändert."""

    def __init__(self, current: dict):
        super().__init__("Der Lead wurde inzwischen geändert.")
        self.current = current


class Duplikat(LeadError):
    """Es gibt schon einen passenden Lead. ``hart`` = gleiche Telefonnummer (nie doppelt anlegbar)."""

    def __init__(self, vorhanden: dict, hart: bool):
        super().__init__("Diese Telefonnummer gehört schon zu einem Lead." if hart
                         else "Ein Lead mit gleicher Firma und gleichem Namen existiert schon.")
        self.vorhanden, self.hart = vorhanden, hart


# --- Normalisierung --------------------------------------------------------------------------------------------------

def telefon_schluessel(nummer: str) -> str:
    """Vergleichsschlüssel einer Telefonnummer: nur Ziffern, deutsche Schreibweisen vereinheitlicht ('' = keine)."""
    raw = str(nummer or "").strip()
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 6:
        return ""
    if raw.startswith("+"):
        return digits
    if digits.startswith("00"):
        return digits[2:]
    if digits.startswith("0"):
        return "49" + digits[1:]
    return digits


def namens_schluessel(firma: str, name: str) -> str:
    """Vergleichsschlüssel aus Firma und Name: klein, ohne Rechtsform und Sonderzeichen ('' = zu wenig Angaben)."""
    def clean(value: str) -> str:
        value = _RECHTSFORM.sub(" ", str(value or "").lower())
        return re.sub(r"[^a-z0-9äöüß]+", " ", value).strip()
    f, n = clean(firma), clean(name)
    return f"{f}|{n}" if f and n else ""


def _text(value, limit: int, name: str) -> str:
    value = str(value or "").strip()
    if len(value) > limit:
        raise LeadError(f"'{name}' ist zu lang (höchstens {limit} Zeichen).")
    return value


def _datum(value, name: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    try:
        return datetime.date.fromisoformat(value[:10]).isoformat()
    except ValueError as exc:
        raise LeadError(f"'{name}' muss ein Datum im Format JJJJ-MM-TT sein.") from exc


def _zeitpunkt(value, name: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    try:
        return datetime.datetime.fromisoformat(value[:16]).strftime("%Y-%m-%dT%H:%M")
    except ValueError as exc:
        raise LeadError(f"'{name}' muss Datum und Uhrzeit im Format JJJJ-MM-TTTHH:MM sein.") from exc


def _stammdaten(fields: dict, teilweise: bool = False) -> dict:
    clean: dict = {}
    for key, limit in FELDER.items():
        if key in fields or not teilweise:
            clean[key] = _text(fields.get(key), limit, key)
    if clean.get("email") and not _EMAIL.match(clean["email"]):
        raise LeadError("Die E-Mail-Adresse ist ungültig.")
    if clean.get("website") and not re.match(r"^(https?://)?[\w.-]+\.[a-z]{2,}(/\S*)?$", clean["website"], re.I):
        raise LeadError("Die Website ist ungültig (nur http/https-Adressen).")
    if "telefon_status" in fields or not teilweise:
        status = fields.get("telefon_status") or "unklar"
        if status not in TELEFON_STATUS:
            raise LeadError("telefon_status muss geschaeftlich, privat oder unklar sein.")
        clean["telefon_status"] = status
    if "projekt_id" in fields or not teilweise:
        projekt = str(fields.get("projekt_id") or "").strip()
        if projekt and not _ID.match(projekt):
            raise LeadError("Ungültige Projekt-ID.")
        clean["projekt_id"] = projekt
    return clean


# --- Speicher --------------------------------------------------------------------------------------------------------

def _db():
    """Verbindung mit sichergestellten Tabellen (inklusive ``anrufe``, damit beide in einer Transaktion liegen)."""
    cm, c = anrufe._db()
    path = str(anrufe.persist.db_path())
    if path not in _ready:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS leads (
              id TEXT PRIMARY KEY, request_id TEXT UNIQUE, version INTEGER NOT NULL, created REAL NOT NULL,
              updated REAL NOT NULL, status TEXT NOT NULL, telefon_key TEXT NOT NULL DEFAULT '',
              namens_key TEXT NOT NULL DEFAULT '', data TEXT NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS leads_telefon ON leads(telefon_key) WHERE telefon_key <> '';
            CREATE INDEX IF NOT EXISTS leads_name ON leads(namens_key);
            CREATE INDEX IF NOT EXISTS leads_status ON leads(status, updated DESC);
            CREATE TABLE IF NOT EXISTS lead_aktivitaeten (
              id TEXT PRIMARY KEY, request_id TEXT UNIQUE, lead_id TEXT NOT NULL, created REAL NOT NULL,
              art TEXT NOT NULL, data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS lead_akt_lead ON lead_aktivitaeten(lead_id, created DESC);
            CREATE INDEX IF NOT EXISTS lead_akt_zeit ON lead_aktivitaeten(created DESC);
        """)
        _ready.add(path)
    return cm, c


def _lead(row) -> dict:
    return {"id": row[0], "version": row[1], "erstellt": row[2], "geaendert": row[3], "status": row[4], **json.loads(row[5])}


_COLS = "id, version, created, updated, status, data"


def _holen(c, lead_id: str) -> dict:
    if not _ID.match(str(lead_id or "")):
        raise LeadError("Ungültige Lead-ID.")
    row = c.execute(f"SELECT {_COLS} FROM leads WHERE id=?", (lead_id,)).fetchone()
    if row is None:
        raise NichtGefunden("Lead nicht gefunden.")
    return _lead(row)


def _schreiben(c, lead: dict, now: float) -> dict:
    data = {k: v for k, v in lead.items() if k not in ("id", "version", "erstellt", "geaendert", "status")}
    version = lead["version"] + 1
    c.execute("UPDATE leads SET version=?, updated=?, status=?, telefon_key=?, namens_key=?, data=? WHERE id=?",
              (version, now, lead["status"], telefon_schluessel(data.get("telefon", "")),
               namens_schluessel(data.get("firma", ""), data.get("name", "")), json.dumps(data, ensure_ascii=False), lead["id"]))
    return {**lead, "version": version, "geaendert": now}


def _duplikat(c, telefon_key: str, namens_key: str, ausser: str = "") -> tuple[dict, bool] | None:
    if telefon_key:
        row = c.execute(f"SELECT {_COLS} FROM leads WHERE telefon_key=? AND id<>?", (telefon_key, ausser)).fetchone()
        if row:
            return _lead(row), True
    if namens_key:
        row = c.execute(f"SELECT {_COLS} FROM leads WHERE namens_key=? AND id<>?", (namens_key, ausser)).fetchone()
        if row:
            return _lead(row), False
    return None


def _abschluss(cm, c) -> None:
    cm.__exit__(None, None, None)


# --- Leads -----------------------------------------------------------------------------------------------------------

def anlegen(fields: dict, request_id: str | None = None, duplikat_bestaetigt: bool = False) -> dict:
    """Legt einen Lead an. Gleiche Nummer -> ``Duplikat(hart)``; gleiche Firma+Name -> ``Duplikat`` bis bestätigt."""
    clean = _stammdaten(fields)
    if not (clean["firma"] or clean["name"]):
        raise LeadError("Firma oder Name ist nötig.")
    tkey, nkey = telefon_schluessel(clean["telefon"]), namens_schluessel(clean["firma"], clean["name"])
    cm, c = _db()
    try:
        c.execute("BEGIN IMMEDIATE")
        if request_id:
            row = c.execute(f"SELECT {_COLS} FROM leads WHERE request_id=?", (request_id,)).fetchone()
            if row:
                c.execute("ROLLBACK")
                alt = _lead(row)
                if (namens_schluessel(alt["firma"], alt["name"]), telefon_schluessel(alt["telefon"])) != (nkey, tkey):
                    raise LeadError("Diese request_id gehört schon zu einem anderen Lead.")
                return {**alt, "wiederholt": True}
        dup = _duplikat(c, tkey, nkey)
        if dup and (dup[1] or not duplikat_bestaetigt):
            c.execute("ROLLBACK")
            raise Duplikat(dup[0], dup[1])
        ident, now = "L-" + uuid.uuid4().hex[:10], time.time()
        data = {**clean, "followup_am": "", "termin_beginn": "", "termin_ende": "", "letzte_aktivitaet": None,
                "letzte_aktivitaet_text": "", "anrufe": 0}
        c.execute("INSERT INTO leads (id, request_id, version, created, updated, status, telefon_key, namens_key, data) "
                  "VALUES (?,?,?,?,?,?,?,?,?)",
                  (ident, request_id or None, 1, now, now, "neu", tkey, nkey, json.dumps(data, ensure_ascii=False)))
        c.execute("COMMIT")
    except BaseException:
        if c.in_transaction:
            c.execute("ROLLBACK")
        raise
    finally:
        _abschluss(cm, c)
    return {"id": ident, "version": 1, "erstellt": now, "geaendert": now, "status": "neu", **data, "wiederholt": False}


def aendern(lead_id: str, changes: dict, expected_version: int, duplikat_bestaetigt: bool = False) -> dict:
    """Ändert Stammdaten, Notiz, nächsten Schritt oder Follow-up-Datum. Status ändert nur ``ergebnis``."""
    clean = _stammdaten(changes, teilweise=True)
    if "followup_am" in changes:
        clean["followup_am"] = _datum(changes.get("followup_am"), "followup_am")
    if not clean:
        raise LeadError("Keine Änderung angegeben.")
    cm, c = _db()
    try:
        c.execute("BEGIN IMMEDIATE")
        lead = _holen(c, lead_id)
        if lead["version"] != expected_version:
            c.execute("ROLLBACK")
            raise Konflikt(lead)
        merged = {**lead, **clean}
        if not (merged["firma"] or merged["name"]):
            raise LeadError("Firma oder Name ist nötig.")
        dup = _duplikat(c, telefon_schluessel(merged["telefon"]), namens_schluessel(merged["firma"], merged["name"]), lead["id"])
        if dup and (dup[1] or not duplikat_bestaetigt) and (
                dup[1] or namens_schluessel(lead["firma"], lead["name"]) != namens_schluessel(merged["firma"], merged["name"])):
            c.execute("ROLLBACK")
            raise Duplikat(dup[0], dup[1])
        result = _schreiben(c, merged, time.time())
        c.execute("COMMIT")
    except BaseException:
        if c.in_transaction:
            c.execute("ROLLBACK")
        raise
    finally:
        _abschluss(cm, c)
    return result


def ergebnis(lead_id: str, status: str, expected_version: int, request_id: str | None = None, notiz: str = "",
             followup_am: str = "", naechster_schritt: str = "", termin_beginn: str = "", termin_ende: str = "",
             dauer_s: int | None = None) -> dict:
    """Erfasst das Ergebnis eines Anrufs: Aktivität + neuer Lead-Stand + genau eine Zeile in ``anrufe``, atomar.

    ``follow_up`` verlangt ein Datum, ``meeting_gebucht`` Beginn (und optional Ende, sonst +30 Minuten).
    """
    if status not in STATUS or STATUS[status]["anruf"] is None:
        raise LeadError("Ergebnis muss eines von: " + ", ".join(k for k, v in STATUS.items() if v["anruf"]) + " sein.")
    notiz = _text(notiz, 2000, "notiz")
    schritt = _text(naechster_schritt, 300, "naechster_schritt")
    followup = _datum(followup_am, "followup_am")
    beginn, ende = _zeitpunkt(termin_beginn, "termin_beginn"), _zeitpunkt(termin_ende, "termin_ende")
    if status == "follow_up" and not followup:
        raise LeadError("Für ein Follow-up ist ein Datum nötig.")
    if status == "meeting_gebucht":
        if not beginn:
            raise LeadError("Für „Meeting gebucht“ sind Datum und Uhrzeit nötig.")
        if not ende:
            ende = (datetime.datetime.fromisoformat(beginn) + datetime.timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M")
        if ende <= beginn:
            raise LeadError("Das Ende des Meetings muss nach dem Beginn liegen.")
    if dauer_s is not None and (isinstance(dauer_s, bool) or not isinstance(dauer_s, int) or not 0 <= dauer_s <= 14400):
        raise LeadError("dauer_s muss zwischen 0 und 14400 Sekunden liegen.")
    cm, c = _db()
    try:
        c.execute("BEGIN IMMEDIATE")
        if request_id:
            row = c.execute("SELECT lead_id, id, art, data FROM lead_aktivitaeten WHERE request_id=?", (request_id,)).fetchone()
            if row:
                if row[0] != lead_id or row[2] != "anruf" or json.loads(row[3]).get("status") != status:
                    c.execute("ROLLBACK")
                    raise LeadError("Diese request_id gehört schon zu einer anderen Aktivität.")
                lead = _holen(c, row[0])
                c.execute("ROLLBACK")
                return {"lead": lead, "aktivitaet_id": row[1], "wiederholt": True}
        lead = _holen(c, lead_id)
        if lead["version"] != expected_version:
            c.execute("ROLLBACK")
            raise Konflikt(lead)
        now, akt_id = time.time(), "LA-" + uuid.uuid4().hex[:10]
        titel = STATUS[status]["titel"]
        if status == "follow_up":
            schritt = schritt or "Follow-up-Anruf"
        elif status == "meeting_gebucht":
            schritt = schritt or f"Meeting am {beginn.replace('T', ' ')}"
        akt = {"status": status, "status_vorher": lead["status"], "notiz": notiz, "followup_am": followup,
               "termin_beginn": beginn, "termin_ende": ende, "naechster_schritt": schritt, "dauer_s": dauer_s}
        c.execute("INSERT INTO lead_aktivitaeten (id, request_id, lead_id, created, art, data) VALUES (?,?,?,?,?,?)",
                  (akt_id, request_id or None, lead["id"], now, "anruf", json.dumps(akt, ensure_ascii=False)))
        lead = {**lead, "status": status, "naechster_schritt": schritt, "followup_am": followup if status == "follow_up" else "",
                "termin_beginn": beginn if status == "meeting_gebucht" else "", "termin_ende": ende if status == "meeting_gebucht" else "",
                "letzte_aktivitaet": now, "letzte_aktivitaet_text": titel + (f": {notiz}" if notiz else ""),
                "anrufe": int(lead.get("anrufe") or 0) + 1}
        lead = _schreiben(c, lead, now)
        # Kennzahlen: genau ein Anruf je Aktivität, direkt geschrieben (anruf_erfassen würde einen Kanban-Auftrag anlegen).
        call = {"lead_name": lead["name"], "firma": lead["firma"], "rolle": lead["rolle"],
                "telefon_status": lead.get("telefon_status") or "unklar", "kontakt_quelle": lead.get("quelle", ""),
                "ergebnis": STATUS[status]["anruf"], "notiz": (("Mailbox. " if status == "mailbox" else "") + notiz).strip(),
                "naechster_schritt": schritt, "frist": followup or beginn[:10], "crm_status": "nicht_noetig",
                "dauer_s": dauer_s, "auftrag_id": "", "lead_id": lead["id"]}
        c.execute("INSERT INTO anrufe (id, request_id, projekt_id, created, data) VALUES (?,?,?,?,?)",
                  ("C-" + uuid.uuid4().hex[:10], "lead:" + akt_id, lead.get("projekt_id") or "crm", now,
                   json.dumps(call, ensure_ascii=False)))
        c.execute("COMMIT")
    except BaseException:
        if c.in_transaction:
            c.execute("ROLLBACK")
        raise
    finally:
        _abschluss(cm, c)
    return {"lead": lead, "aktivitaet_id": akt_id, "wiederholt": False}


def notiz_hinzufuegen(lead_id: str, text: str, request_id: str | None = None) -> dict:
    """Hängt eine Notiz als Aktivität an (ändert weder Status noch Stammdaten, zählt nicht als Anruf)."""
    text = _text(text, 2000, "notiz")
    if not text:
        raise LeadError("Die Notiz ist leer.")
    cm, c = _db()
    try:
        c.execute("BEGIN IMMEDIATE")
        alt = c.execute("SELECT lead_id, art, data FROM lead_aktivitaeten WHERE request_id=?", (request_id,)).fetchone() if request_id else None
        if alt:
            if alt[0] != lead_id or alt[1] != "notiz" or json.loads(alt[2]).get("notiz") != text:
                c.execute("ROLLBACK")
                raise LeadError("Diese request_id gehört schon zu einer anderen Aktivität.")
            lead = _holen(c, lead_id)
            c.execute("ROLLBACK")
            return {"lead": lead, "wiederholt": True}
        lead = _holen(c, lead_id)
        now = time.time()
        c.execute("INSERT INTO lead_aktivitaeten (id, request_id, lead_id, created, art, data) VALUES (?,?,?,?,?,?)",
                  ("LA-" + uuid.uuid4().hex[:10], request_id or None, lead["id"], now, "notiz",
                   json.dumps({"notiz": text}, ensure_ascii=False)))
        lead = _schreiben(c, {**lead, "letzte_aktivitaet": now, "letzte_aktivitaet_text": "Notiz: " + text[:120]}, now)
        c.execute("COMMIT")
    except BaseException:
        if c.in_transaction:
            c.execute("ROLLBACK")
        raise
    finally:
        _abschluss(cm, c)
    return {"lead": lead, "wiederholt": False}


def _anrufrang(lead: dict, heute: str) -> tuple:
    """Reihenfolge der Anrufliste: fällige Follow-ups, dann neue Leads, dann am längsten nicht Kontaktierte."""
    fup = lead.get("followup_am") or ""
    if lead["status"] == "follow_up" and fup and fup <= heute:
        return (0, fup, lead["erstellt"])
    if lead["status"] == "neu":
        return (1, "", lead["erstellt"])
    if lead["status"] in ("nicht_erreicht", "mailbox"):
        return (2, "", lead.get("letzte_aktivitaet") or 0)
    if lead["status"] == "follow_up":
        return (3, fup or "9999", lead["erstellt"])
    return (4, "", -(lead.get("letzte_aktivitaet") or lead["erstellt"]))


def _alle(c) -> list[dict]:
    """Der vollständige Leadbestand – ohne Obergrenze. Filter, Suche und Zählung dürfen nie auf einem Ausschnitt laufen."""
    return [_lead(r) for r in c.execute(f"SELECT {_COLS} FROM leads").fetchall()]


def _filtern(leads: list[dict], status: str, suche: str) -> list[dict]:
    if status and status not in STATUS and status != "offen":
        raise LeadError("Unbekannter Status.")
    if status == "offen":
        leads = [x for x in leads if STATUS.get(x["status"], {}).get("offen")]
    elif status:
        leads = [x for x in leads if x["status"] == status]
    q = str(suche or "").strip().lower()[:80]
    if q:
        # Telefonsuche nur, wenn die Eingabe wie eine Nummer aussieht – sonst träfe „Firma 0001“ jede Nummer mit „0001“.
        qd = re.sub(r"\D", "", q) if re.fullmatch(r"[\d\s+/().-]+", q) else ""
        leads = [x for x in leads if q in " ".join(str(x.get(k, "")) for k in ("firma", "name", "rolle", "ort", "email")).lower()
                 or (len(qd) >= 3 and qd in re.sub(r"\D", "", x.get("telefon", "")))]
    heute = datetime.date.today().isoformat()
    # Die ID als letzter Schlüssel macht die Reihenfolge eindeutig (gleiche Zeitstempel, z. B. nach einem Import).
    return sorted(leads, key=lambda x: (_anrufrang(x, heute), x["id"]))


def liste(status: str = "", suche: str = "", limit: int | None = None) -> list[dict]:
    """Alle Leads in Anruf-Reihenfolge, optional nach Status und Suchtext (Firma, Name, Telefon, Ort) gefiltert.

    Ohne ``limit`` kommt der gesamte Treffer zurück. Für die Oberfläche gibt es ``uebersicht`` mit Seiten."""
    cm, c = _db()
    try:
        leads = _alle(c)
    finally:
        _abschluss(cm, c)
    leads = _filtern(leads, status, suche)
    return leads if limit is None else leads[:max(1, int(limit))]


# --- Seiten mit stabiler Reihenfolge ---------------------------------------------------------------------------------
# Die Anruf-Reihenfolge hängt von Status und Datum ab und ändert sich mit jedem erfassten Ergebnis. Blättern per
# Versatz würde dann Leads überspringen oder doppelt zeigen. Deshalb friert ein Schnappschuss die Reihenfolge der IDs
# für eine Filter-/Suchkombination ein; Seiten und die Pfeile im Lead-Detail lesen aus demselben Schnappschuss.
# Die Lead-Daten selbst kommen bei jeder Seite frisch aus der Datenbank.

SEITE_STANDARD, SEITE_MAX = 50, 200
_SNAP_MAX, _SNAP_TTL = 12, 2 * 3600
_snapshots: dict[str, dict] = {}
_snap_lock = threading.Lock()


def _snapshot_holen(token: str, db: str, status: str, suche: str) -> list[str] | None:
    with _snap_lock:
        snap = _snapshots.get(token or "")
        if not snap or snap["db"] != db or snap["filter"] != (status, suche) or time.time() - snap["zeit"] > _SNAP_TTL:
            return None
        snap["zeit"] = time.time()
        return snap["ids"]


def _snapshot_anlegen(db: str, status: str, suche: str, ids: list[str]) -> str:
    token = "S-" + secrets.token_urlsafe(12).replace("_", "-")
    with _snap_lock:
        now = time.time()
        for key in [k for k, v in _snapshots.items() if now - v["zeit"] > _SNAP_TTL]:
            del _snapshots[key]
        while len(_snapshots) >= _SNAP_MAX:
            del _snapshots[min(_snapshots, key=lambda k: _snapshots[k]["zeit"])]
        _snapshots[token] = {"db": db, "filter": (status, suche), "ids": ids, "zeit": now}
    return token


def detail(lead_id: str) -> dict:
    cm, c = _db()
    try:
        lead = _holen(c, lead_id)
        rows = c.execute("SELECT id, created, art, data FROM lead_aktivitaeten WHERE lead_id=? ORDER BY created DESC LIMIT 50",
                         (lead["id"],)).fetchall()
    finally:
        _abschluss(cm, c)
    return {"lead": lead, "aktivitaeten": [{"id": r[0], "zeit": r[1], "art": r[2], **json.loads(r[3])} for r in rows]}


def session_zahlen(seit: float) -> dict:
    """Gemessene Calls seit einem Zeitpunkt (für die Call-Session). Zählt nur gespeicherte Anruf-Aktivitäten."""
    seit = max(float(seit), time.time() - 12 * 3600)
    cm, c = _db()
    try:
        rows = c.execute("SELECT created, data FROM lead_aktivitaeten WHERE art='anruf' AND created>=? ORDER BY created", (seit,)).fetchall()
    finally:
        _abschluss(cm, c)
    nach: dict[str, int] = {k: 0 for k, v in STATUS.items() if v["anruf"]}
    for _, data in rows:
        key = json.loads(data).get("status")
        if key in nach:
            nach[key] += 1
    return {"seit": seit, "calls": len(rows), "nach_status": nach, "letzter": rows[-1][0] if rows else None, "stand": time.time()}


def uebersicht(status: str = "", suche: str = "", offset: int = 0, limit: int = SEITE_STANDARD, snapshot: str = "",
               um: str = "") -> dict:
    """Alles für die CRM-Ansicht in einem Aufruf – seitenweise, aber über den VOLLSTÄNDIGEN Bestand gezählt und gesucht.

    ``snapshot``: Kennung aus einer früheren Antwort; damit bleibt die Reihenfolge beim Blättern stabil. Fehlt sie,
    ist sie abgelaufen oder passt sie nicht zu Filter und Suche, entsteht ein neuer Schnappschuss (``snapshot_neu``).
    ``um``: Lead-ID; die Seite wird so gewählt, dass dieser Lead darauf liegt (``um_position`` = Platz in der Reihenfolge).
    """
    suche = str(suche or "").strip()[:80]
    limit = max(1, min(int(limit), SEITE_MAX))
    offset = max(0, int(offset))
    if um and not _ID.match(um):
        raise LeadError("Ungültige Lead-ID.")
    if status and status not in STATUS and status != "offen":
        raise LeadError("Unbekannter Status.")
    heute = datetime.date.today().isoformat()
    db = str(anrufe.persist.db_path())
    cm, c = _db()
    try:
        zahlen = {k: 0 for k in STATUS}
        for key, anzahl in c.execute("SELECT status, COUNT(*) FROM leads GROUP BY status"):
            zahlen[key] = zahlen.get(key, 0) + anzahl
        faellig = c.execute("SELECT COUNT(*) FROM leads WHERE status='follow_up' AND json_extract(data,'$.followup_am') <> '' "
                            "AND json_extract(data,'$.followup_am') <= ?", (heute,)).fetchone()[0]
        ids = _snapshot_holen(snapshot, db, status, suche)
        neu = ids is None
        if neu:
            ids = [x["id"] for x in _filtern(_alle(c), status, suche)]
            snapshot = _snapshot_anlegen(db, status, suche, ids)
        um_position = None
        if um:
            try:
                um_position = ids.index(um)
                offset = (um_position // limit) * limit
            except ValueError:
                um_position = None
        if offset >= len(ids) and ids:
            offset = ((len(ids) - 1) // limit) * limit
        seite_ids = ids[offset:offset + limit]
        rows = c.execute(f"SELECT {_COLS} FROM leads WHERE id IN ({','.join('?' * len(seite_ids))})", seite_ids).fetchall() if seite_ids else []
    finally:
        _abschluss(cm, c)
    nach_id = {r[0]: _lead(r) for r in rows}
    return {
        "status": [{"id": k, "titel": v["titel"], "offen": v["offen"], "ergebnis": v["anruf"] is not None, "ton": v["ton"]}
                   for k, v in STATUS.items()],
        "leads": [nach_id[i] for i in seite_ids if i in nach_id], "zahlen": zahlen, "gesamt": sum(zahlen.values()),
        "offen": sum(n for k, n in zahlen.items() if STATUS.get(k, {}).get("offen")),
        "followups_faellig": faellig, "heute": heute, "csv_spalten": CSV_SPALTEN,
        "csv_max_bytes": CSV_MAX_BYTES, "csv_max_zeilen": CSV_MAX_ZEILEN,
        "treffer": len(ids), "offset": offset, "limit": limit, "snapshot": snapshot, "snapshot_neu": neu,
        "um_position": um_position,
    }


# --- CSV-Import ------------------------------------------------------------------------------------------------------

def csv_import(inhalt: str | bytes, ausfuehren: bool = False) -> dict:
    """Prüft (und importiert bei ``ausfuehren``) eine CSV-Datei. Erst werden ALLE Zeilen geprüft; bei einem
    Zeilenfehler wird nichts geschrieben. Duplikate (Nummer oder Firma+Name, auch innerhalb der Datei) werden
    übersprungen und genannt. Spalten: firma, name, rolle, telefon, email, website, notiz (Kopfzeile Pflicht).

    Grenzen: UTF-8, höchstens ``CSV_MAX_BYTES`` (1 MB) und ``CSV_MAX_ZEILEN`` (2000) Datenzeilen. ``inhalt`` als
    Bytes wird streng als UTF-8 gelesen (kein stilles Ersetzen kaputter Zeichen)."""
    if isinstance(inhalt, (bytes, bytearray)):
        if len(inhalt) > CSV_MAX_BYTES:
            raise LeadError(f"Die CSV-Datei ist zu groß (höchstens {CSV_MAX_BYTES // 1_000_000} MB).")
        try:
            text = bytes(inhalt).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise LeadError(f"Die CSV-Datei ist kein gültiges UTF-8 (Byte {exc.start + 1}). Bitte als „CSV UTF-8“ speichern.") from exc
    elif isinstance(inhalt, str):
        text = inhalt
        if len(text.encode("utf-8", "surrogatepass")) > CSV_MAX_BYTES:
            raise LeadError(f"Die CSV-Datei ist zu groß (höchstens {CSV_MAX_BYTES // 1_000_000} MB).")
    else:
        raise LeadError("Die CSV-Datei ist leer.")
    if not text.strip():
        raise LeadError("Die CSV-Datei ist leer.")
    if "\x00" in text or "\ufffd" in text:
        raise LeadError("Die CSV-Datei enthält ungültige Zeichen (kein sauberes UTF-8). Bitte als „CSV UTF-8“ speichern.")
    text = text.lstrip("\ufeff")
    try:
        dialect = csv.Sniffer().sniff(text[:4000], delimiters=";,\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    try:
        header = [str(h or "").strip().lower() for h in (reader.fieldnames or [])]
    except csv.Error as exc:
        raise LeadError(f"Die CSV-Datei ist nicht lesbar ({exc}).") from exc
    unknown = [h for h in header if h and h not in CSV_SPALTEN]
    if not header or not ({"firma", "name"} & set(header)):
        raise LeadError("Kopfzeile fehlt. Erwartet: " + ", ".join(CSV_SPALTEN) + " (mindestens firma oder name).")
    if unknown:
        raise LeadError("Unbekannte Spalten: " + ", ".join(unknown) + ". Erlaubt: " + ", ".join(CSV_SPALTEN) + ".")
    if len(set(header)) != len(header):
        raise LeadError("Die Kopfzeile nennt eine Spalte doppelt.")
    try:
        zeilen = list(enumerate(reader, start=2))  # alles lesen, bevor irgendetwas geprüft oder geschrieben wird
    except csv.Error as exc:
        raise LeadError(f"Die CSV-Datei ist nicht lesbar (Zeile {reader.line_num}: {exc}).") from exc
    belegt = [(nr, row) for nr, row in zeilen if any(str(v or "").strip() for k, v in row.items() if k)]
    if len(belegt) > CSV_MAX_ZEILEN:
        raise LeadError(f"Zu viele Zeilen: {len(belegt)} (höchstens {CSV_MAX_ZEILEN}).")
    gut, fehler, doppelt = [], [], []
    seen_t, seen_n = set(), set()
    cm, c = _db()
    try:
        for nr, row in belegt:
            raw = {str(k or "").strip().lower(): (v or "") for k, v in row.items() if k}
            try:
                if None in row:  # mehr Felder als Spalten: sonst gingen Angaben still verloren
                    raise LeadError("Mehr Felder als Spalten in der Kopfzeile.")
                clean = _stammdaten({k: raw.get(k, "") for k in CSV_SPALTEN})
                if not (clean["firma"] or clean["name"]):
                    raise LeadError("Firma oder Name ist nötig.")
            except LeadError as exc:
                fehler.append({"zeile": nr, "grund": str(exc)})
                continue
            tkey, nkey = telefon_schluessel(clean["telefon"]), namens_schluessel(clean["firma"], clean["name"])
            label = clean["firma"] or clean["name"]
            if (tkey and tkey in seen_t) or (nkey and nkey in seen_n):
                doppelt.append({"zeile": nr, "lead": label, "grund": "doppelt in der Datei"})
                continue
            dup = _duplikat(c, tkey, nkey)
            if dup:
                doppelt.append({"zeile": nr, "lead": label, "grund": "gleiche Telefonnummer vorhanden" if dup[1] else "gleiche Firma und gleicher Name vorhanden",
                                "vorhanden_id": dup[0]["id"]})
                continue
            if tkey:
                seen_t.add(tkey)
            if nkey:
                seen_n.add(nkey)
            gut.append((clean, tkey, nkey))
        report = {"neu": len(gut), "fehler": fehler[:100], "duplikate": doppelt[:200], "fehler_gesamt": len(fehler),
                  "duplikate_gesamt": len(doppelt), "importiert": 0, "zeilen": len(belegt)}
        if fehler or not ausfuehren or not gut:
            return report
        c.execute("BEGIN IMMEDIATE")
        now = time.time()
        for index, (clean, tkey, nkey) in enumerate(gut):
            data = {**clean, "quelle": clean.get("quelle") or "CSV-Import", "followup_am": "", "termin_beginn": "",
                    "termin_ende": "", "letzte_aktivitaet": None, "letzte_aktivitaet_text": "", "anrufe": 0}
            c.execute("INSERT INTO leads (id, request_id, version, created, updated, status, telefon_key, namens_key, data) "
                      "VALUES (?,?,?,?,?,?,?,?,?)",
                      ("L-" + uuid.uuid4().hex[:10], None, 1, now + index / 1000, now, "neu", tkey, nkey,
                       json.dumps(data, ensure_ascii=False)))
        c.execute("COMMIT")
        report["importiert"] = len(gut)
        return report
    except BaseException:
        if c.in_transaction:
            c.execute("ROLLBACK")
        raise
    finally:
        _abschluss(cm, c)


# --- Kalender --------------------------------------------------------------------------------------------------------

def termin_vorlage(lead: dict) -> dict:
    """Erstellt für ein gebuchtes Meeting eine Kalender-VORLAGE (Freigabe nötig, nichts wird ungefragt angelegt).

    Ohne verbundenen Google-Kalender bleibt der Termin nur am Lead gespeichert; das wird ausdrücklich gemeldet.
    """
    if not lead.get("termin_beginn"):
        return {"status": "kein_termin", "hinweis": "Am Lead ist kein Termin gespeichert."}
    try:
        from .tools import google
        if not google.is_connected():
            return {"status": "nicht_verbunden",
                    "hinweis": "Google-Kalender ist nicht verbunden. Der Termin ist am Lead gespeichert, steht aber nicht im Kalender."}
        from . import approvals
        wer = " · ".join(x for x in (lead.get("firma"), lead.get("name")) if x)
        vorlage = approvals.vorlage("termin_erstellen", {
            "titel": f"Meeting: {wer}"[:200], "beginn": lead["termin_beginn"], "ende": lead["termin_ende"],
            "beschreibung": f"Gebucht im Jarvis-CRM (Lead {lead['id']})." + (f" Telefon: {lead['telefon']}" if lead.get("telefon") else "")})
        return {"status": "vorlage", "freigabe_id": vorlage["id"],
                "hinweis": "Kalender-Vorlage erstellt. Der Termin wird erst nach deiner Freigabe angelegt."}
    except Exception as exc:  # Kalender darf die Ergebniserfassung nie verhindern
        return {"status": "fehler", "hinweis": f"Kalender-Vorlage nicht erstellt ({type(exc).__name__}). Der Termin ist am Lead gespeichert."}
