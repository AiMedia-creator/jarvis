"""Laufzeit für Jarvis-Skills: überprüfbare Definitionen, Aktivierung und Werkzeug-Rahmen im Code.

Ein Jarvis-Skill ist eine Markdown-*Anleitung* (jarvis/skills, ~/.jarvis/skills, importiert aus GitHub). Ausführbar
wird sie nur über registrierte Jarvis-Werkzeuge – das Lesen der Datei allein führt nichts aus. Diese Laufzeit
ergänzt die Anleitungen um eine prüfbare Definition und setzt Regeln im Code durch, nicht nur im Prompt:

- **Aktivierung:** Importierte Skills (fremde Anleitungen) sind aus, bis der Nutzer sie einschaltet. Deaktivierte
  Skills stehen nicht im Katalog, und `lies_skill` verweigert sie.
- **Werkzeug-Rahmen:** Sobald Claude eine Skill liest, gilt für den Rest des Zugs ihr Rahmen. Importierte Skills
  dürfen nur ihre deklarierten (`werkzeuge:` / `allowed-tools:`) und registrierten Werkzeuge nutzen – ohne
  Deklaration nur lesende. Eigene Skills mit Deklaration ebenso. Mitgelieferte bleiben ohne Zusatzrahmen.
  Außenwirksames läuft in jedem Fall weiter über Vorlage + Freigabe (approvals.wrap).
- **Ausführung als Auftrag:** `skill_starten` (Fähigkeit) führt eine Skill als eigenen Job in einem eigenen
  Gespräch aus – unabhängig von der Sprachsitzung, mit Zeitlimit, Abbruch und Wiederaufnahme.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field

from . import config, skills

ALWAYS_ALLOWED = {"lies_skill"}  # Anleitung weiterlesen ist nie eine Aktion
DEFAULT_TIMEOUT = 600


@dataclass
class SkillDefinition:
    id: str
    name: str
    beschreibung: str
    version: str
    herkunft: str            # mitgeliefert | eigen | importiert
    quelle: str              # Pfad oder GitHub-Repo
    commit: str              # festgelegter Commit (importiert) bzw. Inhalts-Hash
    art: str                 # anleitung
    werkzeuge: list[str]     # verfügbare, registrierte Werkzeuge, die die Skill nutzen darf/soll
    fehlende_werkzeuge: list[str]
    rahmen: str              # "frei" (alle registrierten), "deklariert", "nur_lesen"
    berechtigungen: dict[str, list[str]] = field(default_factory=dict)
    bestaetigung: str = "Außenwirksames nur nach Vorlage und deiner Freigabe."
    eingabe: dict = field(default_factory=lambda: {"type": "object", "properties": {
        "auftrag": {"type": "string", "description": "was mit der Skill erledigt werden soll"}}, "required": ["auftrag"]})
    zeitlimit_s: int = DEFAULT_TIMEOUT
    abbrechbar: bool = True
    aktiviert: bool = True
    sperrgrund: str = ""
    verdachtsstellen: int = 0

    def public(self) -> dict:
        return asdict(self)


def _declared(meta: dict) -> list[str] | None:
    raw = meta.get("werkzeuge", meta.get("allowed-tools"))
    if raw is None:
        return None
    items = raw if isinstance(raw, list) else re.split(r"[,\s]+", str(raw))
    return [str(x).strip() for x in items if str(x).strip()]


def _origin(skill: skills.Skill) -> dict:
    if skill.pfad is None:
        return {}
    meta = skill.pfad.parent / ".herkunft.json"
    try:
        return json.loads(meta.read_text(encoding="utf-8")) if meta.is_file() else {}
    except (OSError, ValueError):
        return {}


def _settings() -> tuple[set[str], set[str]]:
    on = {x.strip() for x in str(config.get("skills_aktiv", "") or "").split(",") if x.strip()}
    off = {x.strip() for x in str(config.get("skills_aus", "") or "").split(",") if x.strip()}
    return on, off


def skill_id(skill: skills.Skill) -> str:
    origin = _origin(skill)
    if skill.herkunft == "importiert":
        return f"importiert:{origin.get('quelle', '?')}/{skill.name}"
    return f"{skill.herkunft}:{skill.name}"


def enabled(skill: skills.Skill) -> bool:
    on, off = _settings()
    sid = skill_id(skill)
    if sid in off:
        return False
    return sid in on if skill.herkunft == "importiert" else True


def set_enabled(sid: str, value: bool) -> None:
    if not re.fullmatch(r"[a-z]+:[A-Za-z0-9_./-]{1,160}", sid):
        raise ValueError("ungültige Skill-ID")
    on, off = _settings()
    (on.add if value else on.discard)(sid)
    (off.discard if value else off.add)(sid)
    config.save("skills_aktiv", ",".join(sorted(on)))
    config.save("skills_aus", ",".join(sorted(off)))


def _mentioned(skill: skills.Skill, names: set[str]) -> list[str]:
    return sorted(n for n in names if re.search(r"\b" + re.escape(n) + r"\b", skill.instructions))


def define(skill: skills.Skill, tools: dict) -> SkillDefinition:
    """Prüfbare Definition einer Skill gegen die *tatsächlich* registrierten Werkzeuge."""
    from . import policy

    registered = set(tools)
    declared = _declared(skill.meta)
    origin = _origin(skill)
    if declared is not None:
        allowed = sorted(set(declared) & registered)
        missing = sorted(set(declared) - registered)
        rahmen = "deklariert"
    elif skill.herkunft == "importiert":
        allowed = sorted(n for n in _mentioned(skill, registered) if policy.kind(n, {}, tools.get(n)) == "read")
        missing, rahmen = [], "nur_lesen"
    else:
        known = registered | policy.READ | policy.LOCAL
        allowed = sorted(set(_mentioned(skill, known)) & registered)
        missing = sorted(set(_mentioned(skill, known)) - registered)
        rahmen = "frei"
    perms: dict[str, list[str]] = {"lesen": [], "lokal": [], "schreiben": [], "extern": []}
    for name in allowed:
        k = policy.kind(name, {}, tools.get(name))
        perms[{"read": "lesen", "local": "lokal", "external": "extern"}.get(k, "schreiben")].append(name)
    digest = hashlib.sha256(skill.instructions.encode("utf-8")).hexdigest()[:12]
    try:
        timeout = int(skill.meta.get("zeitlimit", DEFAULT_TIMEOUT))
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    is_on = enabled(skill)
    funde = len(origin.get("funde", []) or [])
    return SkillDefinition(
        id=skill_id(skill), name=skill.name, beschreibung=skill.description,
        version=str(skill.meta.get("version") or "1"),
        herkunft=skill.herkunft,
        quelle=origin.get("quelle") or (str(skill.pfad.relative_to(skill.pfad.parents[1])) if skill.pfad else ""),
        commit=(origin.get("commit") or "")[:40] or f"inhalt:{digest}",
        art="anleitung", werkzeuge=allowed, fehlende_werkzeuge=missing, rahmen=rahmen, berechtigungen=perms,
        zeitlimit_s=max(30, min(3600, timeout)), aktiviert=is_on,
        sperrgrund="" if is_on else ("Importierte Anleitung eines Dritten – erst prüfen und aktivieren."
                                     if skill.herkunft == "importiert" else "Vom Nutzer deaktiviert."),
        verdachtsstellen=funde,
    )


def load_active() -> dict[str, skills.Skill]:
    """Nur aktivierte Skills: nur diese stehen im Katalog und sind über lies_skill lesbar."""
    return {name: s for name, s in skills.load_all().items() if enabled(s)}


def scope_for(name: str, tools: dict) -> set[str] | None:
    """Werkzeug-Rahmen, der nach dem Lesen einer Skill gilt; None = kein Zusatzrahmen."""
    skill = skills.load_all().get(name)
    if skill is None:
        return None
    definition = define(skill, tools)
    if definition.rahmen == "frei":
        return None
    return set(definition.werkzeuge) | ALWAYS_ALLOWED


def definitions(tools: dict) -> list[dict]:
    return [define(s, tools).public() for s in skills.load_all().values()]
