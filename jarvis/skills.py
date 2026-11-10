"""Skills: Markdown-Anleitungen fuer bestimmte Aufgaben.

Zwei Formate (beide gleichzeitig nutzbar):
1. Einzeldatei (bisher):     <ordner>/<name>.md
2. agentskills.io-Standard:  <ordner>/<name>/SKILL.md (+ optionale Zusatzdateien, z. B. references/*.md)
   – das Format von OpenJarvis, Hermes Agent, OpenClaw und Claude-Skills.

Kopf (Frontmatter):
    ---
    name: morgen-briefing
    description: Kurzer Tagesueberblick ...
    ---
    Schritte ...

Mitgelieferte Skills: jarvis/skills/. Eigene: ~/.jarvis/skills/ (gleicher Name ueberschreibt).
Importierte (python -m jarvis --skill-installieren …): ~/.jarvis/skills/importiert/ – Vertrauensstufe "importiert".
Importierte Skills sind reine Anleitungen: Skripte daraus werden weder kopiert noch ausgefuehrt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import config

BUILTIN_DIR = Path(__file__).resolve().parent / "skills"
MAX_NAME = 64
MAX_DESCRIPTION = 1024
TEXT_SUFFIXES = {".md", ".txt"}


@dataclass
class Skill:
    name: str
    description: str
    instructions: str
    herkunft: str = "mitgeliefert"          # mitgeliefert | eigen | importiert
    pfad: Path | None = None
    dateien: list[str] = field(default_factory=list)  # Zusatzdateien (relativ), nur Text
    meta: dict = field(default_factory=dict)


def user_dir() -> Path:
    d = config.data_dir() / "skills"
    d.mkdir(exist_ok=True)
    return d


def imported_dir() -> Path:
    d = user_dir() / "importiert"
    d.mkdir(exist_ok=True)
    return d


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Kleiner YAML-Teilmengen-Parser: key: value, Folded/Literal-Bloecke (> |), einfache Listen, verschachtelte
    Schluessel werden als Text uebernommen. Reicht fuer SKILL.md-Koepfe, ohne PyYAML als Abhaengigkeit."""
    if not text.startswith("---"):
        return {}, text
    rest = text[3:].lstrip("\r\n")
    end = re.search(r"^---\s*$", rest, re.M)
    if not end:
        return {}, text
    header, body = rest[:end.start()], rest[end.end():]
    meta: dict = {}
    lines = header.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^([A-Za-z0-9_-]+)\s*:\s*(.*)$", line)
        i += 1
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if value in (">", "|", ">-", "|-", ">+", "|+") or (value == "" and i < len(lines) and lines[i].startswith((" ", "\t"))):
            block = []
            while i < len(lines) and (lines[i].startswith((" ", "\t")) or not lines[i].strip()):
                block.append(lines[i].strip())
                i += 1
            if block and all(b.startswith("- ") or not b for b in block):
                meta[key] = [b[2:].strip().strip("'\"") for b in block if b]
            else:
                joiner = "\n" if value.startswith("|") else " "
                meta[key] = joiner.join(b for b in block if b or joiner == "\n").strip()
            continue
        if value.startswith("[") and value.endswith("]"):
            meta[key] = [v.strip().strip("'\"") for v in value[1:-1].split(",") if v.strip()]
        else:
            meta[key] = value.strip("'\"")
    return meta, body


def _valid_name(name: str) -> bool:
    return 0 < len(name) <= MAX_NAME and re.fullmatch(r"[a-z0-9](?:[a-z0-9]|-(?!-))*", name) is not None \
        and not name.endswith("-")


def _parse(path: Path, herkunft: str = "mitgeliefert") -> Skill:
    text = path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)
    is_dir_skill = path.name.lower() == "skill.md"
    default = path.parent.name if is_dir_skill else path.stem
    name = str(meta.get("name") or default).strip()
    if herkunft == "importiert" and not _valid_name(name):
        raise ValueError(f"ungültiger Skill-Name '{name}'")
    description = str(meta.get("description", "")).strip()[:MAX_DESCRIPTION]
    files = []
    if is_dir_skill:
        for extra in sorted(path.parent.rglob("*")):
            if extra.is_file() and extra != path and extra.suffix.lower() in TEXT_SUFFIXES:
                files.append(extra.relative_to(path.parent).as_posix())
    return Skill(name, description, body.strip(), herkunft, path, files[:40], meta)


def _scan_dir(directory: Path, herkunft: str) -> list[Path]:
    if not directory.is_dir():
        return []
    found = sorted(directory.glob("*.md"))
    found += sorted(p for p in directory.glob("*/SKILL.md") if p.parent.name != "importiert")
    found += sorted(p for p in directory.glob("*/skill.md") if p.parent.name != "importiert")
    return found


def load_all() -> dict[str, Skill]:
    skills: dict[str, Skill] = {}
    sources = [(BUILTIN_DIR, "mitgeliefert")]
    imp = user_dir() / "importiert"
    if imp.is_dir():
        sources += [(d, "importiert") for d in sorted(imp.iterdir()) if d.is_dir()]
    sources.append((user_dir(), "eigen"))  # eigene zuletzt: gleicher Name ueberschreibt
    for directory, herkunft in sources:
        for path in _scan_dir(directory, herkunft):
            try:
                skill = _parse(path, herkunft)
            except (OSError, ValueError) as exc:
                print(f"[skills] {path} uebersprungen: {exc}")
                continue
            skills[skill.name] = skill
    return skills


def read_file(skill: Skill, datei: str) -> str:
    if not skill.pfad or skill.pfad.name.lower() != "skill.md":
        raise ValueError("Diese Skill hat keine Zusatzdateien")
    root = skill.pfad.parent.resolve()
    target = (root / datei).resolve()
    if root not in target.parents or target.suffix.lower() not in TEXT_SUFFIXES or not target.is_file():
        raise ValueError(f"Datei nicht gefunden. Verfügbar: {', '.join(skill.dateien) or 'keine'}")
    return target.read_text(encoding="utf-8", errors="replace")[:20000]


def catalog(skills: dict[str, Skill]) -> str:
    def line(s: Skill) -> str:
        tag = " [importiert]" if s.herkunft == "importiert" else ""
        return f"- {s.name}{tag}: {s.description[:300]}"
    return "\n".join(line(s) for s in skills.values())
