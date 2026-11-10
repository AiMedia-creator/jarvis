"""Skills aus oeffentlichen Quellen importieren (Idee aus OpenJarvis ``skills/importer`` + ``sources``, Apache-2.0).

Quellen: ``hermes`` (NousResearch/hermes-agent, ~150 Skills), ``openclaw`` (openclaw/skills, Community) oder ein
beliebiges GitHub-Repo (``owner/repo`` oder https-URL) mit SKILL.md-Dateien (agentskills.io-Standard).

Sicherheit (bewusst strenger als das Original):
- Kopiert werden NUR Text (SKILL.md, *.md, *.txt) – keine Skripte, keine Binaerdateien, nichts wird ausgefuehrt.
- Jede Datei laeuft durch den Injection-Scanner; Funde der Stufe "hoch" verhindern die Installation
  (ausser mit ``--trotzdem``). Herkunft, Commit und Funde stehen in ``.herkunft.json`` neben der Skill.
- Importierte Skills sind im Katalog markiert und gelten als Anleitung eines Dritten, nicht als Vollmacht.

CLI:
    python -m jarvis --skill-quelle hermes            # Quelle laden/aktualisieren (git clone --depth 1)
    python -m jarvis --skills-suchen recherche        # in geladenen Quellen suchen
    python -m jarvis --skill-installieren hermes:arxiv
    python -m jarvis --skill-entfernen arxiv
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from . import config, schutz, skills

KNOWN = {
    "hermes": "https://github.com/NousResearch/hermes-agent.git",
    "openclaw": "https://github.com/openclaw/skills.git",
    "anthropic": "https://github.com/anthropics/skills.git",
}
MAX_FILE = 200_000
MAX_FILES = 40


@dataclass
class Found:
    name: str
    quelle: str
    pfad: Path
    beschreibung: str
    kategorie: str


def cache_root() -> Path:
    d = config.data_dir() / "skill-cache"
    d.mkdir(exist_ok=True)
    return d


def _source_id(source: str) -> tuple[str, str]:
    source = source.strip()
    if source in KNOWN:
        return source, KNOWN[source]
    m = re.fullmatch(r"(?:https://github\.com/)?([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?", source)
    if not m:
        raise ValueError("Quelle: hermes, openclaw, anthropic oder ein GitHub-Repo als owner/repo")
    owner, repo = m.group(1), m.group(2)
    return f"{owner}-{repo}".lower(), f"https://github.com/{owner}/{repo}.git"


def sync(source: str) -> Path:
    sid, url = _source_id(source)
    target = cache_root() / sid
    if (target / ".git").is_dir():
        subprocess.run(["git", "-C", str(target), "fetch", "--depth", "1", "origin"], check=True,
                       capture_output=True, timeout=180)
        subprocess.run(["git", "-C", str(target), "reset", "--hard", "FETCH_HEAD"], check=True,
                       capture_output=True, timeout=60)
    else:
        if target.exists():
            shutil.rmtree(target)
        subprocess.run(["git", "clone", "--depth", "1", url, str(target)], check=True, capture_output=True, timeout=300)
    return target


def _commit(path: Path) -> str:
    try:
        return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True,
                              timeout=10).stdout.strip()
    except Exception:
        return ""


def list_source(sid: str) -> list[Found]:
    root = cache_root() / sid
    out, seen = [], set()
    for pattern in ("SKILL.md", "skill.md"):
        for md in sorted(root.rglob(pattern)):
            if ".git" in md.parts or md.parent in seen or md.is_symlink():
                continue
            seen.add(md.parent)
            try:
                meta, _ = skills.parse_frontmatter(md.read_text(encoding="utf-8", errors="replace")[:20000])
            except OSError:
                continue
            name = str(meta.get("name") or md.parent.name).strip()
            cat = md.parent.parent.relative_to(root).as_posix() if md.parent != root else ""
            out.append(Found(name, sid, md.parent, str(meta.get("description", ""))[:300], cat))
    return out


def search(term: str = "") -> list[Found]:
    term = term.lower().strip()
    hits = []
    for d in sorted(cache_root().iterdir()) if cache_root().exists() else []:
        if d.is_dir():
            for f in list_source(d.name):
                if not term or term in f"{f.name} {f.beschreibung} {f.kategorie}".lower():
                    hits.append(f)
    return hits


def _resolve(spec: str) -> Found:
    if ":" not in spec:
        raise ValueError("Format quelle:name, z. B. hermes:arxiv")
    source, name = spec.split(":", 1)
    sid, _ = _source_id(source)
    if not (cache_root() / sid).is_dir():
        sync(source)
    matches = [f for f in list_source(sid) if f.name == name.strip() or f.pfad.name == name.strip()]
    if not matches:
        raise ValueError(f"Skill '{name}' in Quelle '{source}' nicht gefunden (erst --skills-suchen nutzen)")
    return matches[0]


def install(spec: str, force: bool = False) -> str:
    found = _resolve(spec)
    if not skills._valid_name(found.name):
        raise ValueError(f"Ungültiger Skill-Name '{found.name}' (nur a-z, 0-9, Bindestrich)")
    root = (cache_root() / found.quelle).resolve()
    base = found.pfad.resolve()
    if found.pfad.is_symlink() or root not in base.parents:
        raise ValueError("Installation abgebrochen: Skill-Ordner ist ein Symlink oder liegt außerhalb der Quelle.")

    def inside(p: Path) -> bool:
        # Symlinks nie folgen: ein fremdes Repo koennte sonst lokale Dateien (z. B. ~/.jarvis.json) einschleusen.
        rel = p.relative_to(found.pfad)
        if any((found.pfad / Path(*rel.parts[:i + 1])).is_symlink() for i in range(len(rel.parts))):
            return False
        return base == p.resolve().parent or base in p.resolve().parents

    entries = [p for p in sorted(found.pfad.rglob("*")) if ".git" not in p.parts]
    links = [p.relative_to(found.pfad).as_posix() for p in entries if p.is_symlink()]
    files = [p for p in entries if not p.is_symlink() and p.is_file() and inside(p)]
    texts = [p for p in files if p.suffix.lower() in skills.TEXT_SUFFIXES and p.stat().st_size <= MAX_FILE][:MAX_FILES]
    if links and any(l.lower().endswith(("skill.md",)) for l in links):
        raise ValueError("Installation abgebrochen: SKILL.md ist ein Symlink.")
    skipped = [p.relative_to(found.pfad).as_posix() for p in files if p not in texts] + links
    findings = []
    for p in texts:
        result = schutz.scan(p.read_text(encoding="utf-8", errors="replace"))
        for kind, snippet in result.findings:
            findings.append({"datei": p.relative_to(found.pfad).as_posix(), "muster": kind, "auszug": snippet})
        if result.level == "hoch" and not force:
            raise ValueError(f"Installation abgebrochen: {p.name} enthält verdächtige Anweisungen "
                             f"({', '.join(sorted({k for k, _ in result.findings}))}). Erst prüfen; mit --trotzdem erzwingen.")
    target = skills.imported_dir() / found.quelle / found.name
    if target.exists():
        shutil.rmtree(target)
    for p in texts:
        dest = target / p.relative_to(found.pfad)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(p.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    if not (target / "SKILL.md").exists() and (target / "skill.md").exists():
        (target / "skill.md").rename(target / "SKILL.md")
    (target / ".herkunft.json").write_text(json.dumps({
        "quelle": found.quelle, "pfad": found.pfad.relative_to(cache_root() / found.quelle).as_posix(),
        "commit": _commit(cache_root() / found.quelle), "installiert": time.strftime("%Y-%m-%d %H:%M"),
        "nicht_kopiert": skipped, "funde": findings}, ensure_ascii=False, indent=2), encoding="utf-8")
    note = f" ({len(skipped)} Nicht-Text-Dateien wie Skripte bewusst nicht übernommen)" if skipped else ""
    warn = f" Achtung: {len(findings)} Verdachtsstellen, siehe .herkunft.json." if findings else ""
    return f"Skill '{found.name}' aus {found.quelle} installiert nach {target}{note}.{warn}"


def remove(name: str) -> str:
    for d in skills.imported_dir().glob(f"*/{name}"):
        if d.is_dir():
            shutil.rmtree(d)
            return f"Skill '{name}' entfernt."
    raise ValueError(f"Keine importierte Skill '{name}' gefunden")
