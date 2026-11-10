"""Durable, local-only Jarvis memory.

SQLite is the source of truth. This module deliberately uses deterministic
hashed word features rather than a neural embedding service: recall remains
local, reproducible and honest about what it is.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from . import config

_MAX_FACT = 8000
_MAX_USER = 8000
_MAX_ASSISTANT = 16000
_MAX_LIMIT = 50
_SEARCH_CANDIDATES = 200
_STOP = set("der die das den dem des ein eine einer einen und oder ist sind ich du wir sie es von mit fuer für auf zu im in an am als dass nicht nur auch wie was wer bitte doch noch".split())
_SECRET_PATTERNS = [
    re.compile(r"(?i)\b(?:passwort|password|kennwort|api[-_ ]?key|token|secret|zugangsdaten|auth(?:entication)?)[\s:=]+[^\s,;]+"),
    re.compile(r"(?i)\b(?:otp|tan|pin|passcode|code)\s*(?:ist|is|:|=)\s*[A-Za-z0-9._-]{3,}"),
    re.compile(r"\b(?:\d[ -]?){13,19}\b"),
    re.compile(r"\b[A-Za-z0-9+/]{24,}={0,2}\b"),
    re.compile(r"(?i)\b(?:sk|pk|rk|ghp|xoxb|xoxp|AIza)[-_][A-Za-z0-9_-]{10,}\b"),
    re.compile(r"\b[A-Z0-9]{20,}\b"),
]
_LEGACY_SCOPE = "__legacy__"
_filesystem_baseline: float | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _id() -> str:
    return uuid.uuid4().hex


def _redact(text: Any) -> str:
    text = "" if text is None else str(text)
    for pat in _SECRET_PATTERNS:
        text = pat.sub("[redacted]", text)
    return text


_CONFLICT_SIMILARITY = 0.55
_CATEGORY_ALIASES = {"vorliebe": "preference", "vorlieben": "preference", "präferenz": "preference",
                     "praeferenz": "preference", "präferenzen": "preference", "projekt": "project",
                     "projekte": "project", "notiz": "note", "notizen": "note", "unternehmen": "firma",
                     "company": "firma", "business": "firma", "firmenprofil": "firma", "lernen": "lektion",
                     "feedback": "lektion", "lesson": "lektion"}
# Immer im Kopf (unabhaengig von der Suchanfrage): feste Vorlieben, Firmenprofil, gelernte Arbeitsregeln.
STANDING = ("preference", "firma", "lektion")


def _is_valid(valid_until: Any) -> bool:
    if not valid_until:
        return True
    try:
        return datetime.fromisoformat(str(valid_until)) > datetime.now(timezone.utc)
    except ValueError:
        return True


def _contains_secret(text: str) -> bool:
    return _redact(text) != text


def _terms(text: str) -> list[str]:
    return [w.lower() for w in re.findall(r"[\wÄÖÜäöüß]{2,}", text.lower()) if w.lower() not in _STOP]


def _vector(text: str, dims: int = 96) -> list[float]:
    vals = [0.0] * dims
    for word in _terms(text):
        digest = hashlib.blake2b(word.encode("utf-8"), digest_size=8).digest()
        idx = int.from_bytes(digest, "big") % dims
        vals[idx] += 1.0
    norm = sum(x * x for x in vals) ** 0.5
    return [round(x / norm, 8) if norm else 0.0 for x in vals]


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


class MemoryStore:
    """Thread-safe facade; every operation owns and closes its SQLite connection."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        owner_scope: str = "local",
        installation_scope: str = "default",
    ):
        global _filesystem_baseline
        if _filesystem_baseline is None:
            # Legacy-file migration is a startup compatibility step. Files
            # created after the process first opened memory belong to the
            # live filesystem tool, not to every later isolated database.
            _filesystem_baseline = time.time()
        self.path = Path(path or (config.data_dir() / "memory.sqlite3")).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.owner_scope, self.installation_scope = str(owner_scope), str(installation_scope)
        self._lock = threading.RLock()
        self._cache: OrderedDict[tuple, dict] = OrderedDict()
        self._last_revision = -1
        self._init_db()
        self._migrate_filesystem()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Yield one configured connection and always close it on every path."""
        c = sqlite3.connect(str(self.path), timeout=10.0, isolation_level=None)
        c.row_factory = sqlite3.Row
        try:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA busy_timeout=10000")
            c.execute("PRAGMA synchronous=NORMAL")
            yield c
        except BaseException:
            if c.in_transaction:
                c.rollback()
            raise
        finally:
            c.close()

    @staticmethod
    def _columns(c: sqlite3.Connection, table: str) -> set[str]:
        return {str(row[1]) for row in c.execute(f"PRAGMA table_info({table})").fetchall()}

    def _init_db(self) -> None:
        with self._lock, self._connect() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS facts (
                  id TEXT PRIMARY KEY, content TEXT NOT NULL, category TEXT NOT NULL,
                  source TEXT NOT NULL, owner_scope TEXT NOT NULL, installation_scope TEXT NOT NULL,
                  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, vector TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS episodes (
                  id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, user_text TEXT NOT NULL,
                  assistant_text TEXT NOT NULL, status TEXT NOT NULL, owner_scope TEXT NOT NULL,
                  installation_scope TEXT NOT NULL, created_at TEXT NOT NULL, vector TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memory_events (
                  id TEXT PRIMARY KEY, event_type TEXT NOT NULL, entity_id TEXT, category TEXT,
                  metadata TEXT NOT NULL, created_at TEXT NOT NULL,
                  owner_scope TEXT NOT NULL, installation_scope TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memory_meta (
                  key TEXT PRIMARY KEY, value TEXT NOT NULL
                );
                INSERT OR IGNORE INTO memory_meta(key, value) VALUES ('revision', '0');
                CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(id UNINDEXED, content, category);
                CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(id UNINDEXED, conversation_id, user_text, assistant_text);
                CREATE INDEX IF NOT EXISTS episodes_conv_time ON episodes(conversation_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS events_time ON memory_events(created_at DESC);
                """
            )
            # Older databases had unscoped rows/events. Never make those rows
            # visible to a newly scoped store during migration.
            for table in ("facts", "episodes"):
                columns = self._columns(c, table)
                if "owner_scope" not in columns:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN owner_scope TEXT NOT NULL DEFAULT '{_LEGACY_SCOPE}'")
                if "installation_scope" not in columns:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN installation_scope TEXT NOT NULL DEFAULT '{_LEGACY_SCOPE}'")
            # Gedächtnis-Metadaten (seit Brain-Umbau): Gültigkeit, Konfidenz, Einwilligung, Widerspruch.
            columns = self._columns(c, "facts")
            for column, ddl in (("valid_until", "TEXT"), ("confidence", "REAL NOT NULL DEFAULT 1.0"),
                                ("consent", "TEXT NOT NULL DEFAULT 'explicit'"), ("conflicts_with", "TEXT NOT NULL DEFAULT ''")):
                if column not in columns:
                    c.execute(f"ALTER TABLE facts ADD COLUMN {column} {ddl}")
            # Gedächtnis-Center: Bestätigung, Herkunft (Client, Ursprungs-Gespräch) und Versionsverlauf.
            columns = self._columns(c, "facts")
            for column, ddl in (("confirmed_at", "TEXT"), ("origin_episode", "TEXT NOT NULL DEFAULT ''"),
                                ("client", "TEXT NOT NULL DEFAULT ''")):
                if column not in columns:
                    c.execute(f"ALTER TABLE facts ADD COLUMN {column} {ddl}")
            # Gesprächsarchiv: Kanal (text, echtzeit, mcp …), letzte Änderung und Client-Referenz gegen Doppelte.
            columns = self._columns(c, "episodes")
            for column, ddl in (("kanal", "TEXT NOT NULL DEFAULT 'text'"), ("updated_at", "TEXT"), ("client_ref", "TEXT")):
                if column not in columns:
                    c.execute(f"ALTER TABLE episodes ADD COLUMN {column} {ddl}")
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS fact_versions (
                  id TEXT PRIMARY KEY, fact_id TEXT NOT NULL, content TEXT NOT NULL, category TEXT NOT NULL,
                  source TEXT NOT NULL, changed_at TEXT NOT NULL, reason TEXT NOT NULL,
                  owner_scope TEXT NOT NULL, installation_scope TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS fact_versions_fact ON fact_versions(fact_id, changed_at DESC);
                CREATE UNIQUE INDEX IF NOT EXISTS episodes_client_ref ON episodes(client_ref) WHERE client_ref IS NOT NULL;
                """
            )
            columns = self._columns(c, "memory_events")
            if "owner_scope" not in columns:
                c.execute(f"ALTER TABLE memory_events ADD COLUMN owner_scope TEXT NOT NULL DEFAULT '{_LEGACY_SCOPE}'")
            if "installation_scope" not in columns:
                c.execute(f"ALTER TABLE memory_events ADD COLUMN installation_scope TEXT NOT NULL DEFAULT '{_LEGACY_SCOPE}'")

    def _meta_value(self, c: sqlite3.Connection, key: str) -> str | None:
        row = c.execute("SELECT value FROM memory_meta WHERE key=?", (key,)).fetchone()
        return None if row is None else str(row[0])

    def _revision(self, c: sqlite3.Connection) -> int:
        value = self._meta_value(c, "revision")
        try:
            return int(value or 0)
        except ValueError:
            return 0

    def _migrate_filesystem(self) -> None:
        """Import safe legacy files once per database, without following paths outside the root."""
        root = (config.data_dir() / "memories").expanduser()
        if not root.exists() or not root.is_dir():
            return
        try:
            root_resolved = root.resolve(strict=True)
        except OSError:
            return
        marker = f"filesystem_migrated:{root_resolved}"
        with self._lock, self._connect() as c:
            if self._meta_value(c, marker) is not None:
                return
        try:
            candidates = sorted(root.rglob("*"))
        except OSError:
            return
        inspected = 0
        try:
            for candidate in candidates:
                if inspected >= 1000:
                    break
                try:
                    resolved = candidate.resolve(strict=True)
                    if not resolved.is_relative_to(root_resolved) or not resolved.is_file():
                        continue
                    if _filesystem_baseline is not None and resolved.stat().st_mtime > _filesystem_baseline:
                        continue
                    if resolved.stat().st_size > _MAX_FACT:
                        continue
                    inspected += 1
                    text = resolved.read_text(encoding="utf-8", errors="replace")
                except (OSError, UnicodeError):
                    continue
                # Do not import credential-bearing legacy files, even redacted.
                if text.strip() and not _contains_secret(text):
                    self.remember(text, category=f"legacy:{resolved.relative_to(root_resolved)}", source="filesystem")
            with self._lock, self._connect() as c:
                c.execute("INSERT OR REPLACE INTO memory_meta(key,value) VALUES (?,?)", (marker, _now()))
        except OSError:
            pass

    def _event(
        self,
        c: sqlite3.Connection,
        event_type: str,
        entity_id: str | None,
        category: str | None,
        metadata: dict | None = None,
    ) -> None:
        safe = {
            str(k): _redact(str(v))[:300]
            for k, v in (metadata or {}).items()
            if k not in {"content", "text", "user_text", "assistant_text", "secret"}
        }
        # This update and the event insert are always inside the caller's
        # explicit write transaction, so another process sees one revision.
        c.execute("UPDATE memory_meta SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")
        c.execute(
            """INSERT INTO memory_events
               (id,event_type,entity_id,category,metadata,created_at,owner_scope,installation_scope)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                _id(),
                event_type,
                entity_id,
                category,
                json.dumps(safe),
                _now(),
                self.owner_scope,
                self.installation_scope,
            ),
        )

    def _invalidate(self) -> None:
        self._cache.clear()

    def remember(self, content: str, category: str = "note", source: str = "user", *,
                 valid_until: str | None = None, confidence: float = 1.0, consent: str = "explicit",
                 skip_on_conflict: bool = False, client: str = "", origin_episode: str = "") -> dict:
        """Speichert eine Erinnerung mit Quelle, Scope, Gültigkeit, Konfidenz und Einwilligung.

        Identischer Inhalt wird nicht doppelt gespeichert. Ähnliche, aber abweichende Aussagen derselben Kategorie
        werden NICHT still überschrieben: Beide bleiben erhalten und werden als Widerspruch markiert.
        """
        content = str(content or "").strip()
        if not content or len(content) > _MAX_FACT:
            raise ValueError("note must be non-empty and at most 8000 characters")
        if _contains_secret(content):
            raise ValueError("secrets, credentials, codes and payment data cannot be stored")
        category = _redact(str(category or "note"))[:120]
        category = _CATEGORY_ALIASES.get(category.strip().lower(), category)
        if valid_until:
            try:
                parsed = datetime.fromisoformat(str(valid_until).replace("Z", "+00:00"))
            except ValueError:
                raise ValueError("valid_until must be an ISO date like 2026-12-31")
            valid_until = (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).isoformat(timespec="seconds")
        confidence = max(0.0, min(1.0, float(confidence)))
        consent = consent if consent in {"explicit", "policy"} else "explicit"
        now, ident = _now(), _id()
        vector = _vector(content)
        normalized = " ".join(content.lower().split())
        with self._lock, self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            rows = c.execute(
                "SELECT id,content,vector,valid_until FROM facts WHERE category=? AND owner_scope=? AND installation_scope=?",
                (category, self.owner_scope, self.installation_scope),
            ).fetchall()
            conflicts = []
            for row in rows:
                if not _is_valid(row["valid_until"]):
                    continue
                if " ".join(str(row["content"]).lower().split()) == normalized:
                    c.execute("ROLLBACK")
                    return {"id": row["id"], "content": row["content"], "category": category,
                            "duplicate": True, "conflicts": []}
                try:
                    similar = _cosine(vector, json.loads(row["vector"]))
                except Exception:
                    similar = 0.0
                if similar >= _CONFLICT_SIMILARITY:
                    conflicts.append({"id": row["id"], "content": row["content"]})
            if conflicts and skip_on_conflict:
                c.execute("ROLLBACK")
                return {"id": None, "content": content, "category": category, "skipped": True, "conflicts": conflicts}
            conflict_ids = ",".join(item["id"] for item in conflicts)
            c.execute(
                """INSERT INTO facts (id,content,category,source,owner_scope,installation_scope,created_at,updated_at,
                   vector,valid_until,confidence,consent,conflicts_with,client,origin_episode)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (ident, content, category, str(source)[:80], self.owner_scope, self.installation_scope, now, now,
                 json.dumps(vector), valid_until, confidence, consent, conflict_ids,
                 _redact(str(client or ""))[:80], str(origin_episode or "")[:64]),
            )
            for item in conflicts:
                c.execute(
                    "UPDATE facts SET conflicts_with=TRIM(conflicts_with || ',' || ?, ',') WHERE id=? AND owner_scope=? AND installation_scope=?",
                    (ident, item["id"], self.owner_scope, self.installation_scope),
                )
            c.execute("INSERT INTO facts_fts(id,content,category) VALUES (?,?,?)", (ident, content, category))
            self._event(c, "fact_created", ident, category, {"source": source, "conflicts": len(conflicts),
                                                               "valid_until": valid_until or ""})
            c.execute("COMMIT")
            self._invalidate()
        return {"id": ident, "content": content, "category": category, "updated_at": now, "source": str(source)[:80],
                "valid_until": valid_until, "confidence": confidence, "consent": consent, "conflicts": conflicts}

    def record_turn(self, conversation_id: str, user_text: str, assistant_text: str, status: str = "completed",
                    kanal: str = "text") -> dict:
        uid, aid = _redact(str(user_text))[:_MAX_USER], _redact(str(assistant_text))[:_MAX_ASSISTANT]
        if not uid.strip() and not aid.strip():
            raise ValueError("conversation turn is empty")
        ident, now = _id(), _now()
        conv = str(conversation_id or "haupt")[:200]
        with self._lock, self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            self._insert_episode(c, ident, conv, uid, aid, str(status or "completed")[:40], now, kanal, None)
            self._event(c, "episode_recorded", ident, "conversation", {"conversation_id": conv, "status": status})
            c.execute("COMMIT")
            self._invalidate()
        return {"id": ident, "conversation_id": conv, "user_text": uid, "assistant_text": aid, "status": str(status), "created_at": now}

    # --- Gesprächsarchiv fortlaufend: erst die Äußerung sichern, dann die Antwort ergänzen -------------------

    def _insert_episode(self, c: sqlite3.Connection, ident: str, conv: str, user: str, assistant: str, status: str,
                        now: str, kanal: str, client_ref: str | None) -> None:
        c.execute(
            """INSERT INTO episodes (id,conversation_id,user_text,assistant_text,status,owner_scope,installation_scope,
               created_at,vector,kanal,updated_at,client_ref) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ident, conv, user, assistant, status, self.owner_scope, self.installation_scope, now,
             json.dumps(_vector(user + " " + assistant)), str(kanal or "text")[:24], now, client_ref),
        )
        c.execute("INSERT INTO episodes_fts(id,conversation_id,user_text,assistant_text) VALUES (?,?,?,?)",
                  (ident, conv, user, assistant))

    def begin_turn(self, conversation_id: str, user_text: str, kanal: str = "text", client_ref: str | None = None) -> dict:
        """Sichert die Nutzeräußerung SOFORT (Status pending), bevor Jarvis antwortet.

        Bricht die Verbindung oder der Prozess danach ab, bleibt der Wortlaut erhalten. `client_ref` macht den
        Aufruf wiederholbar: dieselbe Referenz legt nichts doppelt an.
        """
        uid = _redact(str(user_text))[:_MAX_USER]
        if not uid.strip():
            raise ValueError("utterance is empty")
        conv, now, ident = str(conversation_id or "haupt")[:200], _now(), _id()
        ref = str(client_ref)[:80] if client_ref else None
        with self._lock, self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            if ref:
                row = c.execute("SELECT id,status,created_at FROM episodes WHERE client_ref=? AND owner_scope=? AND installation_scope=?",
                                (ref, self.owner_scope, self.installation_scope)).fetchone()
                if row:
                    c.execute("ROLLBACK")
                    return {"id": row["id"], "status": row["status"], "created_at": row["created_at"], "duplicate": True}
            self._insert_episode(c, ident, conv, uid, "", "pending", now, kanal, ref)
            self._event(c, "utterance_saved", ident, "conversation", {"conversation_id": conv, "kanal": kanal})
            c.execute("COMMIT")
            self._invalidate()
        return {"id": ident, "status": "pending", "created_at": now, "duplicate": False}

    def finish_turn(self, episode_id: str, assistant_text: str, status: str = "completed", append: bool = False) -> bool:
        """Ergänzt die Antwort zu einer gesicherten Äußerung und setzt den endgültigen Status."""
        aid = _redact(str(assistant_text or ""))[:_MAX_ASSISTANT]
        with self._lock, self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT conversation_id,user_text,assistant_text FROM episodes WHERE id=? AND owner_scope=? AND installation_scope=?",
                            (str(episode_id), self.owner_scope, self.installation_scope)).fetchone()
            if row is None:
                c.execute("ROLLBACK")
                return False
            if append and row["assistant_text"]:
                aid = (row["assistant_text"] + " " + aid).strip()[:_MAX_ASSISTANT]
            c.execute("UPDATE episodes SET assistant_text=?, status=?, updated_at=?, vector=? WHERE id=?",
                      (aid, str(status or "completed")[:40], _now(), json.dumps(_vector(row["user_text"] + " " + aid)), str(episode_id)))
            c.execute("DELETE FROM episodes_fts WHERE id=?", (str(episode_id),))
            c.execute("INSERT INTO episodes_fts(id,conversation_id,user_text,assistant_text) VALUES (?,?,?,?)",
                      (str(episode_id), row["conversation_id"], row["user_text"], aid))
            self._event(c, "episode_recorded", str(episode_id), "conversation",
                        {"conversation_id": row["conversation_id"], "status": status})
            c.execute("COMMIT")
            self._invalidate()
        return True

    def log_utterance(self, conversation_id: str, role: str, text: str, kanal: str = "echtzeit",
                      client_ref: str | None = None) -> dict:
        """Eine einzelne erkannte Äußerung (z. B. aus dem Echtzeit-Sprachmodus) fortlaufend ablegen.

        Nutzeräußerungen eröffnen einen Archiv-Eintrag; die nächste Jarvis-Äußerung desselben Gesprächs schließt ihn.
        """
        role = "user" if role == "user" else "agent"
        if role == "user":
            # Ältere offene Äußerungen desselben Gesprächs bekamen keine Antwort mehr (Reinreden): Wortlaut bleibt,
            # Status wird "interrupted" – sonst stünden sie bis zum nächsten Neustart als ausstehend da.
            with self._lock, self._connect() as c:
                c.execute("UPDATE episodes SET status='interrupted', updated_at=? WHERE conversation_id=? AND kanal=? "
                          "AND status='pending' AND owner_scope=? AND installation_scope=? AND (client_ref IS NULL OR client_ref<>?)",
                          (_now(), str(conversation_id or "haupt")[:200], str(kanal)[:24], self.owner_scope,
                           self.installation_scope, str(client_ref or "")[:80]))
            saved = self.begin_turn(conversation_id, text, kanal, client_ref)
            return {"id": saved["id"], "status": "gespeichert", "duplicate": saved.get("duplicate", False)}
        clean = _redact(str(text or ""))[:_MAX_ASSISTANT]
        if not clean.strip():
            raise ValueError("utterance is empty")
        conv = str(conversation_id or "haupt")[:200]
        ref = str(client_ref)[:80] if client_ref else None
        with self._lock, self._connect() as c:
            if ref and c.execute("SELECT 1 FROM memory_meta WHERE key=?", ("ref:" + ref,)).fetchone():
                return {"id": None, "status": "gespeichert", "duplicate": True}
            row = c.execute("""SELECT id FROM episodes WHERE conversation_id=? AND kanal=? AND status='pending'
                               AND owner_scope=? AND installation_scope=? ORDER BY created_at DESC, rowid DESC LIMIT 1""",
                            (conv, str(kanal)[:24], self.owner_scope, self.installation_scope)).fetchone()
        if row is not None:
            self.finish_turn(row["id"], clean, "completed", append=True)
            ident = row["id"]
        else:  # Jarvis spricht ohne vorherige Nutzeräußerung (Begrüßung) oder setzt nach einem Werkzeug fort
            with self._lock, self._connect() as c:
                # Nur an einen Eintrag der LAUFENDEN Sitzung anhängen (letzte Änderung < 3 min), nie an alte Gespräche.
                recent = datetime.fromtimestamp(time.time() - 180, timezone.utc).isoformat(timespec="seconds")
                last = c.execute("""SELECT id FROM episodes WHERE conversation_id=? AND kanal=? AND owner_scope=?
                                    AND installation_scope=? AND COALESCE(updated_at, created_at)>=?
                                    ORDER BY created_at DESC, rowid DESC LIMIT 1""",
                                 (conv, str(kanal)[:24], self.owner_scope, self.installation_scope, recent)).fetchone()
            if last is not None:
                self.finish_turn(last["id"], clean, "completed", append=True)
                ident = last["id"]
            else:
                ident = self.record_turn(conv, "", clean, "completed", kanal=kanal)["id"]
        if ref:
            with self._lock, self._connect() as c:
                c.execute("INSERT OR IGNORE INTO memory_meta(key,value) VALUES (?,?)", ("ref:" + ref, _now()))
        return {"id": ident, "status": "gespeichert", "duplicate": False}

    def close_pending(self, older_than_s: float = 0.0) -> int:
        """Beim Start: Äußerungen, deren Antwort nie ankam, als unterbrochen markieren (Wortlaut bleibt)."""
        limit = datetime.fromtimestamp(time.time() - max(0.0, older_than_s), timezone.utc).isoformat(timespec="seconds")
        with self._lock, self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            cur = c.execute("UPDATE episodes SET status='interrupted', updated_at=? WHERE status='pending' AND created_at<=? "
                            "AND owner_scope=? AND installation_scope=?", (_now(), limit, self.owner_scope, self.installation_scope))
            if cur.rowcount:
                self._event(c, "pending_closed", None, "conversation", {"anzahl": cur.rowcount})
            c.execute("COMMIT")
            self._invalidate()
            return cur.rowcount

    def delete_episode(self, ident: str) -> dict:
        """Löscht einen Archiv-Eintrag samt Suchindex und den daraus abgeleiteten Erinnerungen."""
        ident = str(ident)
        with self._lock, self._connect() as c:
            row = c.execute("SELECT id FROM episodes WHERE id=? AND owner_scope=? AND installation_scope=?",
                            (ident, self.owner_scope, self.installation_scope)).fetchone()
            derived = [r["id"] for r in c.execute("SELECT id FROM facts WHERE origin_episode=? AND owner_scope=? AND installation_scope=?",
                                                  (ident, self.owner_scope, self.installation_scope)).fetchall()]
        if row is None:
            return {"deleted": False, "abgeleitet": 0}
        for fact_id in derived:
            self.forget_report(fact_id)
        with self._lock, self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("DELETE FROM episodes_fts WHERE id=?", (ident,))
            c.execute("DELETE FROM episodes WHERE id=? AND owner_scope=? AND installation_scope=?",
                      (ident, self.owner_scope, self.installation_scope))
            self._event(c, "episode_deleted", ident, "conversation", {"abgeleitet": len(derived)})
            c.execute("COMMIT")
            self._invalidate()
        return {"deleted": True, "abgeleitet": len(derived)}

    # --- Dauerhaftes Wissen: korrigieren mit Versionsverlauf, bestätigen, exportieren -------------------------

    def _fact_row(self, c: sqlite3.Connection, ident: str):
        return c.execute("SELECT * FROM facts WHERE id=? AND owner_scope=? AND installation_scope=?",
                         (str(ident), self.owner_scope, self.installation_scope)).fetchone()

    def update_fact(self, ident: str, content: str, category: str | None = None, source: str = "user",
                    reason: str = "korrigiert") -> dict:
        """Korrigiert eine Erinnerung. Die alte Fassung bleibt im Versionsverlauf – nichts wird still überschrieben."""
        content = str(content or "").strip()
        if not content or len(content) > _MAX_FACT:
            raise ValueError("note must be non-empty and at most 8000 characters")
        if _contains_secret(content):
            raise ValueError("secrets, credentials, codes and payment data cannot be stored")
        now = _now()
        with self._lock, self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = self._fact_row(c, ident)
            if row is None:
                c.execute("ROLLBACK")
                raise KeyError(ident)
            new_category = _CATEGORY_ALIASES.get(str(category or row["category"]).strip().lower(),
                                                 _redact(str(category or row["category"]))[:120])
            if row["content"] == content and row["category"] == new_category:
                c.execute("ROLLBACK")
                return {"id": str(ident), "unveraendert": True}
            c.execute("""INSERT INTO fact_versions (id,fact_id,content,category,source,changed_at,reason,owner_scope,installation_scope)
                         VALUES (?,?,?,?,?,?,?,?,?)""",
                      (_id(), str(ident), row["content"], row["category"], row["source"], now, str(reason)[:80],
                       self.owner_scope, self.installation_scope))
            c.execute("UPDATE facts SET content=?, category=?, source=?, updated_at=?, vector=?, confirmed_at=NULL WHERE id=?",
                      (content, new_category, str(source)[:80], now, json.dumps(_vector(content)), str(ident)))
            c.execute("DELETE FROM facts_fts WHERE id=?", (str(ident),))
            c.execute("INSERT INTO facts_fts(id,content,category) VALUES (?,?,?)", (str(ident), content, new_category))
            self._event(c, "fact_updated", str(ident), new_category, {"source": source, "reason": reason})
            c.execute("COMMIT")
            self._invalidate()
        return {"id": str(ident), "content": content, "category": new_category, "updated_at": now, "unveraendert": False}

    def confirm(self, ident: str) -> dict:
        """Der Nutzer bestätigt eine Erinnerung: volle Konfidenz, Zeitpunkt wird festgehalten."""
        now = _now()
        with self._lock, self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = self._fact_row(c, ident)
            if row is None:
                c.execute("ROLLBACK")
                raise KeyError(ident)
            c.execute("UPDATE facts SET confirmed_at=?, confidence=1.0 WHERE id=?", (now, str(ident)))
            self._event(c, "fact_confirmed", str(ident), row["category"], {})
            c.execute("COMMIT")
            self._invalidate()
        return {"id": str(ident), "confirmed_at": now}

    def history(self, ident: str) -> dict:
        """Aktuelle Fassung, frühere Fassungen und Ereignisse einer Erinnerung (neueste zuerst)."""
        with self._lock, self._connect() as c:
            row = self._fact_row(c, ident)
            versions = c.execute("""SELECT content,category,source,changed_at,reason FROM fact_versions
                                    WHERE fact_id=? AND owner_scope=? AND installation_scope=? ORDER BY changed_at DESC, rowid DESC""",
                                 (str(ident), self.owner_scope, self.installation_scope)).fetchall()
            events = c.execute("""SELECT event_type,created_at,metadata FROM memory_events WHERE entity_id=? AND owner_scope=?
                                  AND installation_scope=? ORDER BY created_at DESC LIMIT 30""",
                               (str(ident), self.owner_scope, self.installation_scope)).fetchall()
        if row is None:
            raise KeyError(ident)
        return {"aktuell": self._fact_public(row), "versionen": [dict(v) for v in versions],
                "ereignisse": [{"typ": e["event_type"], "zeit": e["created_at"]} for e in events]}

    @staticmethod
    def _fact_public(row: sqlite3.Row) -> dict:
        keys = row.keys()
        return {"id": str(row["id"]), "content": row["content"], "category": row["category"], "source": row["source"],
                "created_at": row["created_at"], "updated_at": row["updated_at"], "valid_until": row["valid_until"],
                "confidence": row["confidence"], "consent": row["consent"],
                "confirmed_at": row["confirmed_at"] if "confirmed_at" in keys else None,
                "client": row["client"] if "client" in keys else "",
                "origin_episode": row["origin_episode"] if "origin_episode" in keys else "",
                "conflicts": [x for x in str(row["conflicts_with"] or "").split(",") if x],
                "gueltig": _is_valid(row["valid_until"])}

    def center(self, query: str = "", limit: int = 50) -> dict:
        """Daten fürs Gedächtnis-Center: Wissen und Archiv getrennt, mit Herkunft, Zeitstempeln und Speicherstatus."""
        limit = max(1, min(200, int(limit)))
        found = self.search(query, min(_MAX_LIMIT, limit), strict=True) if str(query).strip() else None
        scope = (self.owner_scope, self.installation_scope)
        with self._lock, self._connect() as c:
            if found is None:
                fact_rows = c.execute("SELECT * FROM facts WHERE owner_scope=? AND installation_scope=? ORDER BY updated_at DESC LIMIT ?",
                                      (*scope, limit)).fetchall()
                ep_rows = c.execute("""SELECT id,conversation_id,user_text,assistant_text,status,created_at,kanal,updated_at FROM episodes
                                       WHERE owner_scope=? AND installation_scope=? ORDER BY created_at DESC, rowid DESC LIMIT ?""",
                                    (*scope, limit)).fetchall()
            else:
                def by_ids(table: str, ids: list[str], cols: str):
                    if not ids:
                        return []
                    marks = ",".join("?" * len(ids))
                    rows = {r["id"]: r for r in c.execute(
                        f"SELECT {cols} FROM {table} WHERE id IN ({marks}) AND owner_scope=? AND installation_scope=?",
                        (*ids, *scope)).fetchall()}
                    return [rows[i] for i in ids if i in rows]
                fact_rows = by_ids("facts", [f["id"] for f in found["facts"]], "*")
                ep_rows = by_ids("episodes", [e["id"] for e in found["episodes"]],
                                 "id,conversation_id,user_text,assistant_text,status,created_at,kanal,updated_at")
            versions = dict(c.execute("SELECT fact_id, COUNT(*) FROM fact_versions WHERE owner_scope=? AND installation_scope=? GROUP BY fact_id",
                                      scope).fetchall())
            by_status = dict(c.execute("SELECT status, COUNT(*) FROM episodes WHERE owner_scope=? AND installation_scope=? GROUP BY status",
                                       scope).fetchall())
            by_kanal = dict(c.execute("SELECT kanal, COUNT(*) FROM episodes WHERE owner_scope=? AND installation_scope=? GROUP BY kanal",
                                      scope).fetchall())
            first = c.execute("SELECT MIN(created_at) FROM episodes WHERE owner_scope=? AND installation_scope=?", scope).fetchone()[0]
        facts = [dict(self._fact_public(r), versionen=int(versions.get(r["id"], 0))) for r in fact_rows]
        return {"suche": str(query), "wissen": facts, "archiv": [dict(r) for r in ep_rows],
                "notizdateien": _note_files(str(query)),
                "stand": _now(),
                "zahlen": {**self.stats(), "archiv_nach_status": by_status, "archiv_nach_kanal": by_kanal,
                           "ausstehend": int(by_status.get("pending", 0)), "fehlgeschlagen": int(by_status.get("failed", 0)),
                           "widersprueche": sum(1 for f in facts if f["conflicts"]), "archiv_seit": first},
                "ereignisse": self.events(20)}

    def export(self) -> dict:
        """Vollständiger Export des eigenen Bereichs (Wissen, Versionen, Archiv) – ohne Vektoren."""
        scope = (self.owner_scope, self.installation_scope)
        with self._lock, self._connect() as c:
            facts = [self._fact_public(r) for r in c.execute(
                "SELECT * FROM facts WHERE owner_scope=? AND installation_scope=? ORDER BY created_at", scope).fetchall()]
            versions = [dict(r) for r in c.execute(
                """SELECT fact_id,content,category,source,changed_at,reason FROM fact_versions
                   WHERE owner_scope=? AND installation_scope=? ORDER BY changed_at""", scope).fetchall()]
            episodes = [dict(r) for r in c.execute(
                """SELECT id,conversation_id,user_text,assistant_text,status,created_at,kanal,updated_at FROM episodes
                   WHERE owner_scope=? AND installation_scope=? ORDER BY created_at""", scope).fetchall()]
        return {"format": "jarvis-gedaechtnis/1", "exportiert": _now(), "quelle": str(self.path),
                "wissen": facts, "versionen": versions, "archiv": episodes}

    def rebuild_index(self) -> dict:
        """Suchindex aus den Originaldaten neu aufbauen – der Index ist nie die einzige Kopie."""
        scope = (self.owner_scope, self.installation_scope)
        with self._lock, self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("DELETE FROM facts_fts")
            c.execute("DELETE FROM episodes_fts")
            c.execute("INSERT INTO facts_fts(id,content,category) SELECT id,content,category FROM facts")
            c.execute("INSERT INTO episodes_fts(id,conversation_id,user_text,assistant_text) "
                      "SELECT id,conversation_id,user_text,assistant_text FROM episodes")
            n_f = c.execute("SELECT COUNT(*) FROM facts WHERE owner_scope=? AND installation_scope=?", scope).fetchone()[0]
            n_e = c.execute("SELECT COUNT(*) FROM episodes WHERE owner_scope=? AND installation_scope=?", scope).fetchone()[0]
            self._event(c, "index_rebuilt", None, "index", {"wissen": n_f, "archiv": n_e})
            c.execute("COMMIT")
            self._invalidate()
        return {"wissen": n_f, "archiv": n_e}

    def forget_report(self, ident: str) -> dict:
        """Löscht genau eine Erinnerung und sagt ehrlich, was davon technisch noch übrig sein kann."""
        with self._lock, self._connect() as c:
            row = c.execute(
                "SELECT content FROM facts WHERE id=? AND owner_scope=? AND installation_scope=?",
                (str(ident), self.owner_scope, self.installation_scope),
            ).fetchone()
        if row is None or not self.forget(ident):
            return {"deleted": False, "id": str(ident), "hinweis": "Keine Erinnerung mit dieser ID in deinem Bereich."}
        with self._lock, self._connect() as c:  # Widerspruchs-Verweise auf die gelöschte Notiz entfernen
            c.execute("UPDATE facts SET conflicts_with=TRIM(REPLACE(','||conflicts_with||',', ','||?||',', ','), ',') "
                      "WHERE owner_scope=? AND installation_scope=?", (str(ident), self.owner_scope, self.installation_scope))
        return {"deleted": True, "id": str(ident), "inhalt": str(row["content"])[:200],
                "hinweis": "Die Notiz ist gelöscht. Frühere Gesprächsverläufe, in denen sie vorkam, bleiben bestehen, "
                           "bis sie einzeln gelöscht werden; Datei-Backups deines Rechners können ältere Stände enthalten."}

    def forget(self, ident: str) -> bool:
        ident = str(ident)
        with self._lock, self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            fact = c.execute(
                "SELECT category FROM facts WHERE id=? AND owner_scope=? AND installation_scope=?",
                (ident, self.owner_scope, self.installation_scope),
            ).fetchone()
            if not fact:
                c.execute("ROLLBACK")
                return False
            c.execute("DELETE FROM facts_fts WHERE id=?", (ident,))
            c.execute(
                "DELETE FROM facts WHERE id=? AND owner_scope=? AND installation_scope=?",
                (ident, self.owner_scope, self.installation_scope),
            )
            # Auch frühere Fassungen verschwinden – sonst bliebe der gelöschte Inhalt im Versionsverlauf stehen.
            c.execute("DELETE FROM fact_versions WHERE fact_id=? AND owner_scope=? AND installation_scope=?",
                      (ident, self.owner_scope, self.installation_scope))
            self._event(c, "fact_deleted", ident, fact["category"], {})
            c.execute("COMMIT")
            self._invalidate()
            return True

    def _search_rows(self, c: sqlite3.Connection, query: str, limit: int, strict: bool = False) -> tuple[list[dict], list[dict]]:
        qterms = _terms(query)
        qvec = _vector(query)
        facts, episodes = [], []
        # OR preserves recall when a natural query contains a rare identifying
        # word plus common context. Quoting keeps punctuation from becoming FTS syntax.
        ftsq = " OR ".join('"' + t.replace('"', ' ') + '"' for t in qterms)
        fact_rows = []
        if ftsq:
            try:
                fact_rows = c.execute(
                    """SELECT f.id,f.content,f.category,f.updated_at,f.vector,f.source,f.valid_until,f.confidence,f.conflicts_with,bm25(facts_fts) AS rank
                       FROM facts_fts JOIN facts f ON f.id=facts_fts.id
                       WHERE facts_fts MATCH ? AND f.owner_scope=? AND f.installation_scope=?
                       ORDER BY bm25(facts_fts) ASC LIMIT ?""",
                    (ftsq, self.owner_scope, self.installation_scope, _SEARCH_CANDIDATES),
                ).fetchall()
            except sqlite3.OperationalError:
                fact_rows = []
        if not fact_rows and not (strict and qterms):  # strict: ohne Treffer nichts „Ähnliches“ unterschieben
            fact_rows = c.execute(
                """SELECT id,content,category,updated_at,vector,source,valid_until,confidence,conflicts_with,0 AS rank FROM facts
                   WHERE owner_scope=? AND installation_scope=? ORDER BY updated_at DESC LIMIT ?""",
                (self.owner_scope, self.installation_scope, _SEARCH_CANDIDATES),
            ).fetchall()
        ep_rows = []
        if ftsq:
            try:
                ep_rows = c.execute(
                    """SELECT e.id,e.conversation_id,e.user_text,e.assistant_text,e.status,e.created_at,e.vector,bm25(episodes_fts) AS rank
                       FROM episodes_fts JOIN episodes e ON e.id=episodes_fts.id
                       WHERE episodes_fts MATCH ? AND e.owner_scope=? AND e.installation_scope=?
                       ORDER BY bm25(episodes_fts) ASC LIMIT ?""",
                    (ftsq, self.owner_scope, self.installation_scope, _SEARCH_CANDIDATES),
                ).fetchall()
            except sqlite3.OperationalError:
                ep_rows = []
        if not ep_rows and not (strict and qterms):
            ep_rows = c.execute(
                """SELECT id,conversation_id,user_text,assistant_text,status,created_at,vector,0 AS rank
                   FROM episodes WHERE owner_scope=? AND installation_scope=? ORDER BY created_at DESC LIMIT ?""",
                (self.owner_scope, self.installation_scope, _SEARCH_CANDIDATES),
            ).fetchall()
        now = time.time()

        def rank_strength(row: sqlite3.Row) -> float:
            # FTS5 bm25 is negative (more negative is more relevant). Fallback
            # rows carry zero, so they remain valid when FTS has no matches.
            try:
                return max(0.0, -float(row["rank"]))
            except (TypeError, ValueError):
                return 0.0

        fact_rank_max = max((rank_strength(row) for row in fact_rows), default=0.0)
        episode_rank_max = max((rank_strength(row) for row in ep_rows), default=0.0)

        def score(row: sqlite3.Row, text: str, rank_max: float) -> float:
            lower = text.lower()
            lexical = sum(lower.count(t) for t in qterms) / max(1, len(qterms)) if qterms else 0.0
            fts_relevance = rank_strength(row) / rank_max if rank_max else 0.0
            try:
                vec = _cosine(qvec, json.loads(row["vector"]))
            except Exception:
                vec = 0.0
            try:
                age = max(0.0, now - datetime.fromisoformat(row["updated_at"] if "updated_at" in row.keys() else row["created_at"]).timestamp())
            except Exception:
                age = 0
            return lexical * 0.35 + fts_relevance * 0.40 + max(0.0, vec) * 0.20 + (1 / (1 + age / 86400)) * 0.05

        for row in fact_rows:
            if not _is_valid(row["valid_until"]):
                continue  # abgelaufene Erinnerungen werden nicht mehr verwendet
            facts.append({"id": str(row["id"]), "content": row["content"], "category": row["category"],
                          "updated_at": row["updated_at"], "source": row["source"], "valid_until": row["valid_until"],
                          "confidence": row["confidence"],
                          "conflicts": [x for x in str(row["conflicts_with"] or "").split(",") if x],
                          "_score": score(row, row["content"], fact_rank_max)})
        for row in ep_rows:
            text = row["user_text"] + " " + row["assistant_text"]
            episodes.append({"id": str(row["id"]), "conversation_id": row["conversation_id"], "user_text": row["user_text"], "assistant_text": row["assistant_text"], "status": row["status"], "created_at": row["created_at"], "_score": score(row, text, episode_rank_max)})
        facts.sort(key=lambda item: item.pop("_score"), reverse=True)
        episodes.sort(key=lambda item: item.pop("_score"), reverse=True)
        return facts[:limit], episodes[:limit]

    def standing(self, limit: int = 25) -> list[dict]:
        """Alle gueltigen Dauer-Fakten (Vorlieben, Firma, Lektionen), neueste zuerst."""
        marks = ",".join("?" * len(STANDING))
        with self._lock, self._connect() as c:
            rows = c.execute(f"SELECT id,content,category,source,valid_until FROM facts WHERE category IN ({marks}) "
                             "AND owner_scope=? AND installation_scope=? ORDER BY updated_at DESC LIMIT ?",
                             (*STANDING, self.owner_scope, self.installation_scope, max(1, min(100, limit)))).fetchall()
        return [dict(r) for r in rows if _is_valid(r["valid_until"])]

    def search(self, query: str = "", limit: int = 12, strict: bool = False) -> dict:
        """strict=True liefert nur echte Volltext-Treffer (Gedächtnis-Center, MCP); sonst bei Leere die neuesten Einträge."""
        limit = max(1, min(_MAX_LIMIT, int(limit)))
        key = (str(query), limit, bool(strict))
        with self._lock, self._connect() as c:
            revision = self._revision(c)
            if revision != self._last_revision:
                self._cache.clear()
                self._last_revision = revision
            if key in self._cache:
                self._cache.move_to_end(key)
                return json.loads(json.dumps(self._cache[key]))
            facts, episodes = self._search_rows(c, str(query or ""), limit, strict)
            result = {"facts": facts, "episodes": episodes}
            self._cache[key] = result
            while len(self._cache) > 128:
                self._cache.popitem(last=False)
            return json.loads(json.dumps(result))

    def recent_episodes(self, conversation_id: str | None = None, limit: int = 10) -> list[dict]:
        limit = max(1, min(_MAX_LIMIT, int(limit)))
        with self._lock, self._connect() as c:
            params: list[Any] = [self.owner_scope, self.installation_scope]
            where = "owner_scope=? AND installation_scope=?"
            if conversation_id is not None:
                where += " AND conversation_id=?"
                params.append(str(conversation_id))
            params.append(limit)
            rows = c.execute(
                f"SELECT id,conversation_id,user_text,assistant_text,status,created_at FROM episodes WHERE {where} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
            return [dict(row) for row in reversed(rows)]

    def stats(self) -> dict:
        with self._lock, self._connect() as c:
            scope = (self.owner_scope, self.installation_scope)
            return {
                "facts": c.execute("SELECT COUNT(*) FROM facts WHERE owner_scope=? AND installation_scope=?", scope).fetchone()[0],
                "episodes": c.execute("SELECT COUNT(*) FROM episodes WHERE owner_scope=? AND installation_scope=?", scope).fetchone()[0],
                "events": c.execute("SELECT COUNT(*) FROM memory_events WHERE owner_scope=? AND installation_scope=?", scope).fetchone()[0],
                "path": str(self.path),
            }

    def events(self, limit: int = 30) -> list[dict]:
        limit = max(1, min(_MAX_LIMIT, int(limit)))
        with self._lock, self._connect() as c:
            rows = c.execute(
                """SELECT id,event_type,entity_id,category,metadata,created_at,owner_scope,installation_scope
                   FROM memory_events WHERE owner_scope=? AND installation_scope=? ORDER BY created_at DESC LIMIT ?""",
                (self.owner_scope, self.installation_scope, limit),
            ).fetchall()
            out = []
            for row in rows:
                item = dict(row)
                try:
                    item["metadata"] = json.loads(item["metadata"])
                except Exception:
                    pass
                out.append(item)
            return out

    def snapshot(self, query: str = "", limit: int = 12, conversation_id: str | None = None) -> dict:
        result = self.search(query, limit)
        result["stats"] = self.stats()
        result["events"] = self.events(30)
        if conversation_id is not None:
            result["episodes"] = self.recent_episodes(conversation_id, limit)
        return result


def _note_files(query: str = "") -> list[dict]:
    """Arbeitsnotizen, die das Modell über sein Datei-Werkzeug `memory` in ~/.jarvis/memories ablegt.

    Sie liegen NICHT in SQLite und sind über wissen_suchen nicht auffindbar – das Gedächtnis-Center zeigt sie
    deshalb ausdrücklich als eigenen Bereich (nur lesend, Auszug geschwärzt).
    """
    root = (config.data_dir() / "memories").expanduser()
    out: list[dict] = []
    try:
        base = root.resolve(strict=True)
        files = sorted(p for p in base.rglob("*") if p.is_file() and not p.name.startswith("."))[:100]
    except OSError:
        return out
    needle = [t for t in _terms(query)]
    for path in files:
        try:
            if not path.resolve().is_relative_to(base):
                continue
            stat = path.stat()
            text = path.read_text(encoding="utf-8", errors="replace")[:4000]
        except OSError:
            continue
        if needle and not any(t in text.lower() or t in path.name.lower() for t in needle):
            continue
        out.append({"name": str(path.relative_to(base)), "groesse": stat.st_size,
                    "geaendert": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds"),
                    "auszug": _redact(text)[:600]})
    return out


# Name used by integration notes; it is intentionally a normal Python class.
robustMemoryStore = MemoryStore
_stores: dict[str, MemoryStore] = {}
_stores_lock = threading.RLock()


def store(path: str | Path | None = None) -> MemoryStore:
    p = Path(path or (config.data_dir() / "memory.sqlite3")).expanduser().resolve()
    with _stores_lock:
        if str(p) not in _stores:
            _stores[str(p)] = MemoryStore(p)
        return _stores[str(p)]


def snapshot(query: str = "", limit: int = 12, conversation_id: str | None = None) -> dict:
    return store().snapshot(query, limit, conversation_id)


__all__ = ["MemoryStore", "robustMemoryStore", "store", "snapshot"]
