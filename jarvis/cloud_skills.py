"""Adapter für Anthropic Agent Skills über den bestehenden Anbieter (Claude API, api.anthropic.com).

Geprüft (Doku Stand 2026-11): Agent Skills laufen über die Messages-API mit `container.skills` und dem
Code-Ausführungswerkzeug; claude-haiku-5-5 unterstützt Code-Ausführung. Grenzen, die diesen Adapter prägen:
- Skills laufen in Anthropics Sandbox-Container: **kein Internet**, **kein Zugriff auf Jarvis-Werkzeuge** (Kalender,
  Aufträge, Mails …). Sie eignen sich nur für in sich geschlossene Arbeiten, v. a. Dateien erzeugen (docx, pptx, xlsx, pdf).
- Code-Ausführung ist **nicht ZDR-fähig**: Container-Daten werden bis zu 30 Tage gespeichert; dazu fallen
  Container-Kosten an. Deshalb ist der Adapter **standardmäßig aus**.
- Geladen werden nur Anthropic-eigene Skills, die in `cloud_skills` (~/.jarvis.json, z. B. "docx,pdf") stehen.
  Eigene Skills hochzuladen (/v1/skills) macht Jarvis bewusst nicht: sie wären im ganzen Workspace sichtbar.
Ergebnisse (Dateien) werden über die Files-API nach ~/.jarvis/ausgaben/ geladen; Text geht als fremder Inhalt zurück.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from . import config

ANTHROPIC_SKILLS = {"docx": "Word-Dokument", "pptx": "PowerPoint-Präsentation", "xlsx": "Excel-Tabelle", "pdf": "PDF"}
# Mit dem installierten SDK (anthropic 0.109) gehen Skills über die Beta-Schnittstelle; die Header sind dort nötig.
BETAS = ["code-execution-2025-08-25", "skills-2025-10-02", "files-api-2025-04-14"]
TOOL = {"type": "code_execution_20250825", "name": "code_execution"}
MAX_CONTINUE = 6


def allowed() -> list[str]:
    raw = str(config.get("cloud_skills", "") or "")
    return [s for s in (x.strip().lower() for x in raw.split(",")) if s in ANTHROPIC_SKILLS]


def lock_reason() -> str:
    if not allowed():
        return ("Aus. Anthropic Agent Skills laufen in Anthropics Sandbox (kein Internet, kein Zugriff auf Jarvis-Werkzeuge, "
                "nicht ZDR: Daten bis 30 Tage, Container-Kosten). Freischalten in ~/.jarvis.json: \"cloud_skills\": \"docx,pdf\".")
    if not (config.get("anthropic_key") or __import__("os").getenv("ANTHROPIC_API_KEY")):
        return "ANTHROPIC_API_KEY fehlt."
    return ""


def output_dir() -> Path:
    d = config.data_dir() / "ausgaben"
    d.mkdir(exist_ok=True)
    return d


def run(skill: str, task: str, client=None, check=lambda: None, progress=lambda text: None) -> dict:
    """Führt eine erlaubte Anthropic-Skill aus und lädt erzeugte Dateien herunter."""
    from .agent import MODELS, make_client

    skill = skill.strip().lower()
    if skill not in allowed():
        raise ValueError(f"Cloud-Skill '{skill}' ist nicht freigeschaltet (erlaubt: {', '.join(allowed()) or 'keine'}).")
    client = client or make_client(timeout=300.0)
    model = MODELS.get(config.get("model") or "haiku", config.get("model") or "claude-haiku-5-5")
    messages = [{"role": "user", "content": task[:8000]}]
    container: dict = {"skills": [{"type": "anthropic", "skill_id": skill, "version": "latest"}]}
    texts, file_ids = [], []
    for round_no in range(MAX_CONTINUE):
        check()
        progress(f"Cloud-Skill {skill}: Runde {round_no + 1} in Anthropics Container …")
        response = client.beta.messages.create(model=model, max_tokens=16000, betas=BETAS, container=container,
                                               tools=[TOOL], messages=messages)
        for block in response.content:
            kind = getattr(block, "type", "")
            if kind == "text":
                texts.append(block.text)
            content = getattr(block, "content", None)
            for item in getattr(content, "content", None) or []:
                fid = getattr(item, "file_id", None)
                if fid:
                    file_ids.append(fid)
        container_id = getattr(getattr(response, "container", None), "id", None)
        if response.stop_reason != "pause_turn":
            break
        # Lange Läufe: Antwort zurückgeben und im selben Container weitermachen.
        messages.append({"role": "assistant", "content": response.content})
        if container_id:
            container = {"id": container_id, **container}
    saved = []
    for fid in dict.fromkeys(file_ids):
        check()
        meta = client.beta.files.retrieve_metadata(fid)
        name = re.sub(r"[^A-Za-z0-9._-]", "_", getattr(meta, "filename", "") or f"{fid}.bin")[:120]
        target = output_dir() / f"{time.strftime('%Y%m%d-%H%M%S')}-{name}"
        client.beta.files.download(fid).write_to_file(str(target))
        saved.append(str(target))
    return {"text": ("\n".join(texts)[:3000] or "Fertig.") + (f"\nGespeichert: {', '.join(saved)}" if saved else ""),
            "skill": skill, "dateien": saved, "anbieter": "Anthropic Agent Skills (Container)"}
