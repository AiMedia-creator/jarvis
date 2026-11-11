"""Modell-Anbieter für das Gehirn von Jarvis: Anthropic (Claude), Moonshot (Kimi) und Ollama (lokal).

Alle drei werden über die Anthropic-Messages-Schnittstelle angesprochen – jeweils über eine OFFIZIELLE API:

- Anthropic: https://api.anthropic.com (Standard des SDK), Schlüssel ANTHROPIC_API_KEY.
- Moonshot:  https://api.moonshot.ai/anthropic (laut platform.kimi.ai/docs/api/overview „Anthropic-compatible“),
             Schlüssel MOONSHOT_API_KEY. Modelle laut platform.kimi.ai/docs/api/messages: kimi-k3.1-pro,
             kimi-k3.1-pro-highspeed, kimi-k3.
- Ollama:    http://127.0.0.1:11434 + /v1/messages (laut docs.ollama.com/api/anthropic-compatibility); der lokale
             Server prüft keinen Schlüssel. Welches Modell läuft, bestimmt der Nutzer (``ollama pull …``).

Damit bleiben Streaming, Gesprächsverlauf, Werkzeugaufrufe (tool_use/tool_result) und Fehlerbehandlung des Agenten
unverändert. Was ein Anbieter nicht kann (Server-Werkzeuge wie web_search, das Memory-Werkzeug von Anthropic,
Prompt-Caching, Denktiefe), wird hier pro Anbieter aus der Anfrage entfernt bzw. umgerechnet – nie stillschweigend
an einen anderen Anbieter umgeleitet. Ein Web-Abo (claude.ai, kimi.com, ChatGPT) ersetzt keine API und wird nicht
unterstützt.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from . import config

MOONSHOT_BASE_URL = "https://api.moonshot.ai/anthropic"
OLLAMA_BASE_URL = "http://127.0.0.1:11434"
CLAUDE_FALLBACK = "claude-sonnet-5-5"  # Nebenaufgaben (Gremium, Recherche), wenn das Gehirn kein Claude ist

# Beta-Funktionen der Anthropic-API (Namen aus der offiziellen Doku); jede kann einzeln abgeschaltet werden, falls
# der Server sie ablehnt.
BETA_FALLBACK = "server-side-fallback-2026-07-01"
BETA_BLOCK_BINDING = "thinking-binding-controls-2026-08-01"
# Modelle, die „preserved thinking“ prüfen bzw. serverseitige Ausweichmodelle bei Ablehnung kennen.
_BINDING_MODELS = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5")
_FALLBACK_MODELS = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1", "claude-fable-5-2", "claude-opus-5")


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    key_env: tuple[str, ...]
    key_config: str
    base_url: str | None
    server_tools: bool      # web_search / web_fetch (laufen beim Anbieter)
    memory_tool: bool       # Anthropic-Memory-Werkzeug (memory_20250818)
    cache: bool             # Prompt-Caching (cache_control)
    system_cache: bool      # cache_control auch an Systemblöcken
    effort: tuple[str, ...]  # erlaubte output_config.effort-Werte (leer = weglassen)
    pdf: bool               # PDF als document-Block
    images: bool            # Bilder als image-Block
    needs_key: bool = True
    hinweis: str = ""


PROVIDERS: dict[str, Provider] = {
    "anthropic": Provider("anthropic", "Anthropic (Claude)", ("ANTHROPIC_API_KEY",), "anthropic_key", None,
                          server_tools=True, memory_tool=True, cache=True, system_cache=True,
                          effort=("low", "medium", "high", "xhigh", "max"), pdf=True, images=True),
    "moonshot": Provider("moonshot", "Moonshot (Kimi)", ("MOONSHOT_API_KEY",), "moonshot_key", MOONSHOT_BASE_URL,
                         server_tools=False, memory_tool=False, cache=True, system_cache=False,
                         effort=("low", "high", "max"), pdf=False, images=True,
                         hinweis="Ohne Websuche des Anbieters; Seiten liest Jarvis mit webseite_lesen."),
    "ollama": Provider("ollama", "Ollama (lokal)", (), "ollama_key", OLLAMA_BASE_URL,
                       server_tools=False, memory_tool=False, cache=False, system_cache=False,
                       effort=(), pdf=False, images=False, needs_key=False,
                       hinweis="Läuft auf diesem Rechner; Qualität und Tempo hängen vom gewählten Modell ab."),
}


@dataclass(frozen=True)
class Choice:
    key: str
    provider: str
    model: str
    label: str
    notes: dict = field(default_factory=dict)


def _ollama_model() -> str:
    return (config.get("ollama_model") or "").strip()


def catalog() -> dict[str, Choice]:
    """Auswählbare Modelle (Schlüssel -> Anbieter + Modell-ID). Nur IDs aus den offiziellen Dokus."""
    moonshot = (config.get("moonshot_model") or "").strip()
    items = [
        Choice("opus", "anthropic", "claude-opus-5-5", "Claude Opus 5.5"),
        Choice("sonnet", "anthropic", "claude-sonnet-5-5", "Claude Sonnet 5.5"),
        Choice("haiku", "anthropic", "claude-haiku-5-5", "Claude Haiku 5.5"),
        Choice("kimi", "moonshot", moonshot or "kimi-k3.1-pro", "Kimi K3.1 Pro"),
        Choice("kimi-schnell", "moonshot", "kimi-k3.1-pro-highspeed", "Kimi K3.1 Pro (highspeed)"),
        Choice("kimi-k3", "moonshot", "kimi-k3", "Kimi K3"),
        Choice("ollama", "ollama", _ollama_model() or "", "Ollama: " + (_ollama_model() or "kein Modell gewählt")),
    ]
    return {c.key: c for c in items}


def resolve(name: str | None) -> Choice:
    """Konfigurationswert (Schlüssel wie 'opus' oder rohe Modell-ID) -> Auswahl. Unbekanntes bleibt Anthropic,
    damit ältere Einstellungen (z. B. 'claude-opus-4-8') weiter funktionieren."""
    value = (name or "sonnet").strip()
    found = catalog().get(value)
    if found:
        return found
    if value.startswith("ollama:"):
        model = value.split(":", 1)[1]
        return Choice("ollama", "ollama", model, "Ollama: " + model)
    if value.startswith("kimi-"):
        return Choice(value, "moonshot", value, value)
    return Choice(value, "anthropic", value, value)


def current() -> Choice:
    return resolve(config.get("model") or "sonnet")


def claude_id(name: str | None = None) -> str:
    """Modell-ID für Nebenaufgaben, die nur mit Claude laufen (Gremium, Recherche, Cloud-Skills)."""
    choice = resolve(name or config.get("model") or "sonnet")
    if choice.provider == "anthropic":
        return choice.model
    fallback = config.get("claude_hilfsmodell") or CLAUDE_FALLBACK
    return resolve(fallback).model if resolve(fallback).provider == "anthropic" else CLAUDE_FALLBACK


def api_key(provider: Provider) -> str | None:
    for name in provider.key_env:
        if os.getenv(name):
            return os.getenv(name)
    return config.get(provider.key_config)


def base_url(provider: Provider) -> str | None:
    if provider.id == "moonshot":
        return (os.getenv("MOONSHOT_BASE_URL") or config.get("moonshot_base_url") or MOONSHOT_BASE_URL).rstrip("/")
    if provider.id == "ollama":
        host = os.getenv("OLLAMA_HOST") or config.get("ollama_host") or OLLAMA_BASE_URL
        if not host.startswith(("http://", "https://")):
            host = "http://" + host
        return host.rstrip("/")
    return None


def problem(choice: Choice) -> str | None:
    """Warum diese Auswahl gerade nicht nutzbar ist (None = konfiguriert). Prüft nur Konfiguration, ruft nichts auf."""
    provider = PROVIDERS[choice.provider]
    if provider.id == "ollama" and not choice.model:
        return "Kein Ollama-Modell gewählt: JARVIS_OLLAMA_MODEL setzen (z. B. ein mit 'ollama pull' geladenes Modell)."
    if provider.needs_key and not api_key(provider):
        return f"{' oder '.join(provider.key_env) or provider.key_config} fehlt."
    return None


def make_client(choice: Choice, timeout: float = 60.0, max_retries: int = 1):
    """Anthropic-SDK-Client für den Anbieter der Auswahl (Moonshot/Ollama über deren Anthropic-kompatible API)."""
    import anthropic

    provider = PROVIDERS[choice.provider]
    if provider.id == "anthropic":
        headers = {}
        workspace = config.get("anthropic_workspace") or os.getenv("ANTHROPIC_WORKSPACE_ID")
        if workspace:  # persönliche Schlüssel (sk-ant-usr-…) brauchen die Workspace-ID
            headers["anthropic-workspace-id"] = workspace
        return anthropic.Anthropic(default_headers=headers or None, timeout=timeout, max_retries=max_retries)
    key = api_key(provider)
    if provider.id == "ollama":
        key = key or "ollama"  # lokaler Server prüft keinen Schlüssel; das SDK verlangt aber einen Wert
    if not key:
        raise RuntimeError(problem(choice) or "Zugangsschlüssel fehlt.")
    # Ollama und Moonshot erwarten den Schlüssel als Bearer-Token; x-api-key schickt das SDK zusätzlich.
    return anthropic.Anthropic(api_key=key, base_url=base_url(provider), timeout=timeout, max_retries=max_retries,
                               default_headers={"Authorization": f"Bearer {key}"})


def effort(provider: Provider, wanted: str | None) -> str | None:
    """Denktiefe in die Stufen des Anbieters übersetzen (Moonshot kennt nur low/high/max)."""
    if not wanted or not provider.effort:
        return None
    if wanted in provider.effort:
        return wanted
    mapping = {"medium": "high", "xhigh": "max"}
    mapped = mapping.get(wanted)
    return mapped if mapped in provider.effort else None


def adapt_request(choice: Choice, req: dict, disabled: set[str] | None = None) -> dict:
    """Eine Anthropic-Anfrage des Agenten auf den Anbieter zuschneiden. `disabled`: vom Server abgelehnte Extras."""
    provider = PROVIDERS[choice.provider]
    disabled = disabled or set()
    out = dict(req)
    out["model"] = choice.model
    tools = out.get("tools") or []
    if not provider.server_tools or not provider.memory_tool:
        # Nur eigene Werkzeuge (Name + input_schema). Server-Werkzeuge und das Memory-Werkzeug haben einen „type“.
        tools = [t for t in tools if "input_schema" in t and t.get("type") in (None, "custom")]
    out["tools"] = tools
    if not out["tools"]:
        out.pop("tools")
    system = out.get("system")
    if isinstance(system, list) and (not provider.system_cache or "cache" in disabled):
        out["system"] = [{k: v for k, v in block.items() if k != "cache_control"} for block in system]
    if not provider.cache or "cache" in disabled:
        out.pop("cache_control", None)
    wanted = (out.get("output_config") or {}).get("effort")
    mapped = None if "effort" in disabled else effort(provider, wanted)
    if mapped:
        out["output_config"] = {**(out.get("output_config") or {}), "effort": mapped}
    else:
        out.pop("output_config", None)
    if provider.id == "anthropic":
        betas, extra = [], dict(out.pop("extra_body", None) or {})
        if choice.model.startswith(_BINDING_MODELS) and "block_binding" not in disabled:
            # Jarvis kürzt lange Verläufe von vorn und wechselt Werkzeugsätze (Schnellspur). Statt eines 400-Fehlers
            # verwirft die API dann nur die betroffenen Denkblöcke (offiziell dokumentierter Weg).
            betas.append(BETA_BLOCK_BINDING)
            extra["thinking"] = {"type": "adaptive", "block_binding": {"prefix_mismatch_behavior": "drop_block"}}
        if choice.model.startswith(_FALLBACK_MODELS) and "fallbacks" not in disabled:
            betas.append(BETA_FALLBACK)
            extra["fallbacks"] = "default"  # bei Ablehnung durch die Sicherheitsprüfung: Ausweichmodell des Servers
        if betas:
            out["betas"] = betas
        if extra:
            out["extra_body"] = extra
    return out


def rejected_feature(message: str) -> str | None:
    """Welches Extra hat der Server abgelehnt? (für einen einmaligen Neuversuch ohne dieses Extra)"""
    text = (message or "").lower()
    for key, needles in (("block_binding", ("block_binding", "thinking-binding", "prefix_mismatch")),
                         ("fallbacks", ("fallbacks", "server-side-fallback")),
                         ("effort", ("effort", "output_config")),
                         ("cache", ("cache_control",))):
        if any(n in text for n in needles):
            return key
    return None


def strip_thinking(messages: list) -> list:
    """Denkblöcke entfernen (z. B. nach einem Anbieterwechsel: Signaturen gelten nur beim erzeugenden Modell)."""
    out = []
    for message in messages:
        content = message.get("content")
        if message.get("role") == "assistant" and isinstance(content, list):
            kept = [b for b in content
                    if (b.get("type") if isinstance(b, dict) else getattr(b, "type", "")) not in ("thinking", "redacted_thinking")]
            if not kept:
                kept = [{"type": "text", "text": "…"}]
            out.append({**message, "content": kept})
        else:
            out.append(message)
    return out


def ollama_models(timeout: float = 1.5) -> list[str] | None:
    """Lokal installierte Ollama-Modelle (None = Ollama läuft nicht)."""
    import requests

    try:
        response = requests.get(base_url(PROVIDERS["ollama"]) + "/api/tags", timeout=timeout)
        response.raise_for_status()
        return sorted(str(m.get("name")) for m in response.json().get("models", []) if m.get("name"))
    except Exception:
        return None


def overview(check_ollama: bool = True) -> list[dict]:
    """Für die Einstellungen: alle Auswahlen mit Anbieter und Grund, falls nicht nutzbar (ohne Schlüssel-Werte)."""
    installed = ollama_models() if check_ollama else None
    out = []
    for choice in catalog().values():
        reason = problem(choice)
        if choice.provider == "ollama" and reason is None and check_ollama:
            if installed is None:
                reason = "Ollama läuft nicht (App starten oder 'ollama serve')."
            elif choice.model not in installed and f"{choice.model}:latest" not in installed:
                reason = f"Modell {choice.model} ist nicht installiert ('ollama pull {choice.model}')."
        provider = PROVIDERS[choice.provider]
        out.append({"key": choice.key, "anbieter": provider.id, "anbieter_name": provider.name, "modell": choice.model,
                    "name": choice.label, "verfuegbar": reason is None, "problem": reason, "hinweis": provider.hinweis})
    return out
