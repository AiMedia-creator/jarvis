"""Lokale Mac-Wissensquellen aus OpenJarvis (Apache-2.0), angepasst: Apple Notizen und Obsidian. Nur lesend.

Apple Notizen: liest ~/Library/Group Containers/group.com.apple.notes/NoteStore.sqlite schreibgeschuetzt.
Dafuer braucht das startende Programm (Terminal/Python) "Festplattenvollzugriff" in Systemeinstellungen →
Datenschutz & Sicherheit. Obsidian: Ordner in der Einstellung ``obsidian_vault`` (oder JARVIS_OBSIDIAN_VAULT).
"""

from __future__ import annotations

import gzip
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import config
from . import Tool, ToolFailure, cached, integer, string

NOTES_DB = Path.home() / "Library" / "Group Containers" / "group.com.apple.notes" / "NoteStore.sqlite"
_APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
MAX_NOTE_CHARS = 12000


def _notes_db() -> Path:
    return Path(config.get("apple_notes_db") or NOTES_DB).expanduser()


def _note_text(zdata: bytes | None) -> str:
    if not zdata:
        return ""
    try:
        raw = gzip.decompress(zdata)
    except Exception:
        return ""
    text = raw.decode("utf-8", errors="replace")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[\x00-\x09\x0b\x0c\x0e-\x1f\x7f-\x9f�]+", " ", text)
    text = re.sub(r" {2,}", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _notes_rows() -> list[tuple[str, str, datetime, str]]:
    path = _notes_db()
    if not path.exists():
        raise ToolFailure("Apple Notizen nicht gefunden (kein Mac oder Notizen nie benutzt).")
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3)
    except sqlite3.OperationalError:
        raise ToolFailure("Kein Zugriff auf Apple Notizen. Bitte dem Terminal/Python in Systemeinstellungen → "
                          "Datenschutz & Sicherheit → Festplattenvollzugriff erlauben.") from None
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(ZICCLOUDSYNCINGOBJECT)").fetchall()}
        titles = [f"n.{c}" for c in ("ZTITLE1", "ZTITLE") if c in cols]
        mods = [f"n.{c}" for c in ("ZMODIFICATIONDATE1", "ZMODIFICATIONDATE") if c in cols]
        trash = "AND COALESCE(n.ZMARKEDFORDELETION, 0) = 0" if "ZMARKEDFORDELETION" in cols else ""
        title_expr = f"COALESCE({', '.join(titles)}, '')" if titles else "''"
        mod_expr = f"COALESCE({', '.join(mods)}, 0)" if mods else "0"
        rows = conn.execute(
            f"SELECT n.ZIDENTIFIER, {title_expr}, {mod_expr} AS m, d.ZDATA FROM ZICCLOUDSYNCINGOBJECT n "
            f"JOIN ZICNOTEDATA d ON d.ZNOTE = n.Z_PK WHERE 1=1 {trash} ORDER BY m DESC").fetchall()
    except sqlite3.DatabaseError as exc:
        if "authoriz" in str(exc).lower() or "unable to open" in str(exc).lower():
            raise ToolFailure("Kein Zugriff auf Apple Notizen (Festplattenvollzugriff fehlt).") from None
        raise ToolFailure(f"Apple-Notizen-Datenbank nicht lesbar: {exc}") from None
    finally:
        conn.close()
    return [(r[0] or "", r[1] or "", _APPLE_EPOCH + timedelta(seconds=float(r[2] or 0)), _note_text(r[3]))
            for r in rows]


def _snippet(text: str, terms: list[str], width: int = 220) -> str:
    low = text.lower()
    pos = min((low.find(t) for t in terms if t in low), default=0)
    start = max(0, pos - width // 3)
    return ("…" if start else "") + text[start:start + width].replace("\n", " ") + "…"


@cached(60)
def notizen_suchen(suche: str, anzahl: int = 10) -> str:
    terms = [t for t in str(suche).lower().split() if t]
    if not terms:
        raise ToolFailure("Suchbegriff fehlt")
    hits = []
    for ident, title, mod, text in _notes_rows():
        hay = f"{title}\n{text}".lower()
        if all(t in hay for t in terms):
            hits.append(f"- [{ident}] {title or '(ohne Titel)'} – geändert {mod:%d.%m.%Y}: {_snippet(text, terms)}")
        if len(hits) >= max(1, min(30, int(anzahl or 10))):
            break
    return ("Treffer in Apple Notizen:\n" + "\n".join(hits)) if hits else f"Keine Apple-Notiz enthält '{suche}'."


def notiz_lesen(notiz_id: str) -> str:
    for ident, title, mod, text in _notes_rows():
        if ident == str(notiz_id).strip():
            body = text if len(text) <= MAX_NOTE_CHARS else text[:MAX_NOTE_CHARS] + "\n[... gekürzt]"
            return f"{title or '(ohne Titel)'} (geändert {mod:%d.%m.%Y %H:%M} UTC)\n\n{body}"
    raise ToolFailure("Notiz nicht gefunden – ID aus notizen_suchen verwenden.")


# ---------------------------------------------------------------- Obsidian

def _vault() -> Path:
    raw = config.get("obsidian_vault")
    if not raw:
        raise ToolFailure("Kein Obsidian-Ordner eingerichtet: in ~/.jarvis.json \"obsidian_vault\": \"/Pfad/zum/Vault\".")
    path = Path(raw).expanduser().resolve()
    if not path.is_dir():
        raise ToolFailure(f"Obsidian-Ordner nicht gefunden: {path}")
    return path


def obsidian_configured() -> bool:
    try:
        _vault()
        return True
    except ToolFailure:
        return False


@cached(60)
def obsidian_suchen(suche: str, anzahl: int = 10) -> str:
    vault = _vault()
    terms = [t for t in str(suche).lower().split() if t]
    if not terms:
        raise ToolFailure("Suchbegriff fehlt")
    hits = []
    files = sorted((p for p in vault.rglob("*.md") if ".obsidian" not in p.parts and ".trash" not in p.parts),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files[:5000]:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if all(t in f"{p.stem}\n{text}".lower() for t in terms):
            hits.append(f"- {p.relative_to(vault)}: {_snippet(text, terms)}")
            if len(hits) >= max(1, min(30, int(anzahl or 10))):
                break
    return ("Treffer in Obsidian:\n" + "\n".join(hits)) if hits else f"Keine Obsidian-Notiz enthält '{suche}'."


def obsidian_lesen(pfad: str) -> str:
    vault = _vault()
    path = (vault / str(pfad).strip().lstrip("/")).resolve()
    if vault not in path.parents or path.suffix.lower() != ".md" or not path.is_file():
        raise ToolFailure("Notiz nicht gefunden – relativen Pfad aus obsidian_suchen verwenden.")
    text = path.read_text(encoding="utf-8", errors="replace")
    return f"{path.relative_to(vault)}\n\n" + (text if len(text) <= MAX_NOTE_CHARS else text[:MAX_NOTE_CHARS] + "\n[... gekürzt]")


def tools() -> list[Tool]:
    return [
        Tool("notizen_suchen", "Durchsucht die Apple-Notizen auf dem Mac nach Stichwörtern (alle Wörter müssen vorkommen).",
             {"suche": string("Stichwörter"), "anzahl": integer("höchstens so viele Treffer, Standard 10")},
             notizen_suchen, ["suche"]),
        Tool("notiz_lesen", "Liest eine Apple-Notiz vollständig (ID aus notizen_suchen).",
             {"notiz_id": string("ID in eckigen Klammern aus notizen_suchen")}, notiz_lesen, ["notiz_id"]),
        Tool("obsidian_suchen", "Durchsucht den Obsidian-Vault (Markdown-Notizen) nach Stichwörtern.",
             {"suche": string("Stichwörter"), "anzahl": integer("Standard 10")}, obsidian_suchen, ["suche"]),
        Tool("obsidian_lesen", "Liest eine Obsidian-Notiz (relativer Pfad aus obsidian_suchen).",
             {"pfad": string("relativer Pfad, z. B. 'Projekte/Idee.md'")}, obsidian_lesen, ["pfad"]),
    ]
