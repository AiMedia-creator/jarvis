"""Modell-Anbieter: Anthropic, Moonshot (Kimi) und Ollama über die jeweilige offizielle Messages-Schnittstelle."""


import anthropic
import httpx
import pytest

from jarvis import agent as A
from jarvis import modelle

from helpers import B, Stream, make_agent

BASE = {
    "model": "x", "max_tokens": 16000,
    "system": [{"type": "text", "text": "SYS", "cache_control": {"type": "ephemeral", "ttl": "1h"}}],
    "messages": [{"role": "user", "content": "Hallo"}],
    "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 3},
              {"type": "memory_20250818", "name": "memory"},
              {"name": "wissen_suchen", "description": "d", "input_schema": {"type": "object", "properties": {}}}],
    "output_config": {"effort": "medium"},
    "cache_control": {"type": "ephemeral"},
}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("MOONSHOT_API_KEY", "OLLAMA_HOST", "JARVIS_OLLAMA_MODEL", "JARVIS_MOONSHOT_MODEL", "JARVIS_MODEL",
                 "JARVIS_MOONSHOT_KEY", "JARVIS_CLAUDE_HILFSMODELL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(modelle.config, "load", lambda: {})


def test_auswahl_und_unbekannte_ids():
    assert modelle.resolve("opus").model == "claude-opus-5-5"
    assert modelle.resolve("kimi").provider == "moonshot" and modelle.resolve("kimi").model == "kimi-k3.1-pro"
    assert modelle.resolve("kimi-k3").model == "kimi-k3"
    assert modelle.resolve("ollama:qwen3:8b") == modelle.Choice("ollama", "ollama", "qwen3:8b", "Ollama: qwen3:8b")
    # alte Einstellungen mit roher Claude-ID laufen weiter über Anthropic
    assert modelle.resolve("claude-opus-4-8").provider == "anthropic"


def test_moonshot_nur_eigene_werkzeuge_und_umgerechnete_denktiefe():
    req = modelle.adapt_request(modelle.resolve("kimi"), BASE)
    assert req["model"] == "kimi-k3.1-pro"
    assert [t["name"] for t in req["tools"]] == ["wissen_suchen"]          # keine Server-/Memory-Werkzeuge
    assert req["output_config"] == {"effort": "high"}                     # Moonshot kennt kein „medium“
    assert "cache_control" not in req["system"][0]                         # nur oberster Cache-Marker
    assert req["cache_control"] == {"type": "ephemeral"}
    assert "betas" not in req and "extra_body" not in req


def test_ollama_ohne_cache_und_denktiefe():
    req = modelle.adapt_request(modelle.resolve("ollama:llama3.2"), BASE)
    assert req["model"] == "llama3.2"
    assert "cache_control" not in req and "output_config" not in req
    assert "cache_control" not in req["system"][0]
    assert [t["name"] for t in req["tools"]] == ["wissen_suchen"]


def test_claude_behaelt_alles_und_bekommt_absicherungen():
    req = modelle.adapt_request(modelle.resolve("opus"), BASE)
    assert len(req["tools"]) == 3 and req["system"][0]["cache_control"]["ttl"] == "1h"
    assert req["output_config"] == {"effort": "medium"}
    assert set(req["betas"]) == {modelle.BETA_BLOCK_BINDING, modelle.BETA_FALLBACK}
    assert req["extra_body"]["thinking"]["block_binding"] == {"prefix_mismatch_behavior": "drop_block"}
    assert req["extra_body"]["fallbacks"] == "default"
    # abgelehnte Extras fallen weg, der Rest bleibt
    off = modelle.adapt_request(modelle.resolve("opus"), BASE, {"block_binding", "fallbacks"})
    assert "betas" not in off and "extra_body" not in off and off["output_config"] == {"effort": "medium"}
    # Haiku 5.5: Denkblock-Absicherung, aber keine serverseitigen Ausweichmodelle
    haiku = modelle.adapt_request(modelle.resolve("haiku"), BASE)
    assert haiku["betas"] == [modelle.BETA_BLOCK_BINDING] and "fallbacks" not in haiku["extra_body"]


@pytest.mark.parametrize("message,feature", [
    ("thinking.block_binding: Extra inputs are not permitted", "block_binding"),
    ("fallbacks: unexpected", "fallbacks"),
    ("output_config.effort: invalid value", "effort"),
    ("cache_control is not supported", "cache"),
    ("messages: roles must alternate", None),
])
def test_abgelehnte_extras_erkennen(message, feature):
    assert modelle.rejected_feature(message) == feature


def test_einrichtungsprobleme_ohne_netz(monkeypatch):
    assert "MOONSHOT_API_KEY" in modelle.problem(modelle.resolve("kimi"))
    assert "JARVIS_OLLAMA_MODEL" in modelle.problem(modelle.resolve("ollama"))
    with pytest.raises(RuntimeError):
        modelle.make_client(modelle.resolve("kimi"))
    monkeypatch.setenv("MOONSHOT_API_KEY", "test-schluessel")
    client = modelle.make_client(modelle.resolve("kimi"))
    assert str(client.base_url).rstrip("/") == modelle.MOONSHOT_BASE_URL
    monkeypatch.setenv("JARVIS_OLLAMA_MODEL", "qwen3:8b")
    assert modelle.problem(modelle.resolve("ollama")) is None
    local = modelle.make_client(modelle.resolve("ollama"))
    assert str(local.base_url).rstrip("/") == modelle.OLLAMA_BASE_URL


def test_nebenaufgaben_bleiben_bei_claude(monkeypatch):
    assert modelle.claude_id("kimi") == modelle.CLAUDE_FALLBACK
    assert modelle.claude_id("opus") == "claude-opus-5-5"
    monkeypatch.setenv("JARVIS_CLAUDE_HILFSMODELL", "opus")
    assert modelle.claude_id("ollama:x") == "claude-opus-5-5"


def test_anbieterwechsel_entfernt_nur_denkbloecke(monkeypatch):
    monkeypatch.setenv("MOONSHOT_API_KEY", "test-schluessel")
    agent = make_agent()
    agent.set_model("opus")
    agent.messages = [
        {"role": "user", "content": "Hallo"},
        {"role": "assistant", "content": [{"type": "thinking", "thinking": "", "signature": "abc"},
                                          {"type": "text", "text": "Hi"}]},
    ]
    agent.set_model("kimi")
    assert agent.choice.provider == "moonshot" and agent.model == "kimi-k3.1-pro"
    assert agent.messages[1]["content"] == [{"type": "text", "text": "Hi"}]
    assert agent.client is not None


def test_fehlender_schluessel_meldet_sich_beim_fragen():
    agent = make_agent()
    agent.set_model("kimi")  # MOONSHOT_API_KEY fehlt
    assert agent.client is None
    answer = agent.ask("Wie spät ist es?")
    assert "nicht eingerichtet" in answer and "MOONSHOT_API_KEY" in answer


def _bad_request(text: str) -> anthropic.BadRequestError:
    response = httpx.Response(400, request=httpx.Request("POST", "https://api.example/v1/messages"))
    return anthropic.BadRequestError(text, response=response, body=None)


def test_abgelehntes_extra_wird_einmal_ohne_wiederholt():
    agent = make_agent()
    agent.set_model("opus")
    seen = []

    def stream(**kw):
        seen.append(kw)
        if len(seen) == 1:
            raise _bad_request("thinking.block_binding: Extra inputs are not permitted")
        return Stream(B(stop_reason="end_turn", content=[B(type="text", text="Gut.")]), ["Gut."])

    agent.client = B(beta=B(messages=B(stream=stream)))
    assert agent.ask("Test?") == "Gut."
    assert "extra_body" in seen[0] and "block_binding" in seen[0]["extra_body"].get("thinking", {})
    assert "block_binding" not in (seen[1].get("extra_body") or {}).get("thinking", {})


def test_fremder_anbieter_fehler_klar_benannt():
    exc = anthropic.APIConnectionError(request=httpx.Request("POST", "http://127.0.0.1:11434/v1/messages"))
    exc.jarvis_anbieter = "ollama"
    assert "Ollama" in A.spoken_error(exc)
    exc2 = anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.moonshot.ai/anthropic"))
    exc2.jarvis_anbieter = "moonshot"
    assert "Moonshot" in A.spoken_error(exc2)


def test_einstellungen_stellen_nur_eingerichtete_modelle_um(monkeypatch):
    from jarvis import echtzeit

    saved = {}
    monkeypatch.setattr(echtzeit.config, "save", lambda k, v: saved.__setitem__(k, v))
    monkeypatch.setattr(echtzeit, "status", lambda model_id=None: {"modell": model_id})
    with pytest.raises(ValueError, match="nicht eingerichtet"):
        echtzeit.save_settings({"modell": "kimi"})
    assert saved == {}
    switched = []
    monkeypatch.setenv("MOONSHOT_API_KEY", "test-schluessel")
    echtzeit.save_settings({"modell": "kimi"}, switched.append)
    assert saved == {"model": "kimi"} and switched == ["kimi"]
    with pytest.raises(ValueError, match="unbekannt"):
        echtzeit.save_settings({"modell": "gpt-irgendwas"})
