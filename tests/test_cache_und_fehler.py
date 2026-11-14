"""Quota/Rate-Limit-Unterscheidung, fester Cache-Marker, Gedächtnis im Nutzerzug, Denktiefe, Vorwärmen."""

import types

import anthropic
import httpx
import pytest

from jarvis import agent as A


def api_error(cls, status, message, err_type="invalid_request_error", headers=None):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    body = {"type": "error", "error": {"type": err_type, "message": message}}
    response = httpx.Response(status, request=request, json=body, headers=headers or {})
    return cls(message=message, response=response, body=body)


@pytest.mark.parametrize("exc, kind", [
    (api_error(anthropic.BadRequestError, 400, "Your credit balance is too low to access the Anthropic API."), "guthaben"),
    (api_error(anthropic.APIStatusError, 402, "Payment required", "billing_error"), "guthaben"),
    (api_error(anthropic.RateLimitError, 429, "Number of request tokens has exceeded your rate limit", "rate_limit_error"),
     "rate_limit"),
    # Ein 429 mit "credit"/"billing" im Text bleibt ein Rate-Limit – nie "Guthaben leer"
    (api_error(anthropic.RateLimitError, 429, "billing period rate limit, credit fine", "rate_limit_error"), "rate_limit"),
    (api_error(anthropic.BadRequestError, 400, "You have reached your specified workspace API usage limits"),
     "ausgabenlimit"),
    (api_error(anthropic.InternalServerError, 529, "Overloaded", "overloaded_error"), "ueberlastet"),
    (api_error(anthropic.AuthenticationError, 401, "invalid x-api-key", "authentication_error"), "schluessel"),
    (api_error(anthropic.PermissionDeniedError, 403, "no access", "permission_error"), "zugriff"),
])
def test_fehler_werden_praezise_zugeordnet(exc, kind):
    assert A.classify_error(exc)[0] == kind


def test_rate_limit_nennt_wartezeit_aus_retry_after():
    exc = api_error(anthropic.RateLimitError, 429, "rate limited", "rate_limit_error", headers={"retry-after": "17"})
    assert A.classify_error(exc) == ("rate_limit", 17)
    msg = A.spoken_error(exc)
    assert "17 Sekunden" in msg and "Guthaben" not in msg


def test_unbekannter_400_ist_kein_guthabenproblem():
    exc = api_error(anthropic.BadRequestError, 400, "messages: field required")
    assert A.classify_error(exc)[0] is None and A.spoken_error(exc) is None


def test_systemprompt_hat_festen_cache_marker_und_bleibt_gleich():
    a = A.Agent(channel="web", conversation_id="cache-test")
    a._append_user_turn("Hallo")
    first = a._request()
    a.messages.append({"role": "assistant", "content": "Hallo!"})
    a._append_user_turn("Was steht heute an?")
    second = a._request()
    block = first["system"][0]
    assert block["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert first["system"] == second["system"]  # byte-gleich -> Cache-Treffer
    assert first["tools"] == second["tools"]
    assert second["cache_control"] == {"type": "ephemeral"}  # wandernder Marker für den Verlauf


def test_ohne_cache_bleibt_systemprompt_text():
    a = A.Agent(channel="web", conversation_id="cache-aus")
    a._no_cache = True
    req = a._request()
    assert isinstance(req["system"], str) and "cache_control" not in req


def test_gedaechtnis_nur_neues_und_verlauf_unveraendert(tmp_path):
    a = A.Agent(channel="web", conversation_id="gedaechtnis-test")
    fact = a.memory_store.remember("Erika Beispiel ist Stammkundin.", "note", "user")
    a._append_user_turn("Wer ist Stammkundin?")
    first_turn = a.messages[-1]["content"]
    assert "<gedaechtnis>" in first_turn and "Erika Beispiel" in first_turn
    a.messages.append({"role": "assistant", "content": "Erika."})
    a._append_user_turn("Und Stammkundin sonst?")
    assert a.messages[0]["content"] == first_turn  # früherer Zug nie verändert
    assert "Erika Beispiel" not in a.messages[-1]["content"]  # nichts doppelt
    # Erinnerung korrigiert -> die neue Fassung kommt einmal mit
    a.memory_store.update_fact(fact["id"], "Erika Beispiel ist Stammkundin seit 2024.")
    a.messages.append({"role": "assistant", "content": "Nichts weiter."})
    a._append_user_turn("Was weißt du über Erika?")
    assert "seit 2024" in a.messages[-1]["content"]


def test_nach_kuerzung_kommt_gedaechtnis_wieder(tmp_path):
    a = A.Agent(channel="web", conversation_id="kuerzung-test")
    a.memory_store.remember("Rechnungen immer als PDF.", "note", "user")
    a._append_user_turn("Wie verschicke ich Rechnungen?")
    a.messages.clear()
    a._compacted = True
    a._append_user_turn("Und Rechnungen an Weber?")
    assert "Rechnungen immer als PDF." in a.messages[-1]["content"]


@pytest.mark.parametrize("model, channel, expected", [
    ("claude-haiku-5-5", "web", "low"),
    ("claude-haiku-5-5", "stimme", "low"),
    ("claude-haiku-5-5", "hintergrund", "medium"),
    ("claude-haiku-4-5", "web", None),
])
def test_denktiefe_haiku(model, channel, expected, monkeypatch):
    monkeypatch.delenv("JARVIS_EFFORT", raising=False)
    assert A.effort_for(model, channel) == expected


def test_smalltalk_regel_nur_einmal():
    a = A.Agent(channel="web", conversation_id="regel-test")
    assert a.system.count("Bei Begrüßungen, Bestätigungen") == 1


def test_vorwaermen_schickt_gleiches_praefix_ohne_antwort(monkeypatch):
    a = A.Agent(channel="web", conversation_id="warm-test")
    seen = {}

    def create(**kw):
        seen.update(kw)
        usage = types.SimpleNamespace(input_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=25000)
        return types.SimpleNamespace(usage=usage, content=[], stop_reason="max_tokens")

    monkeypatch.setattr(a.client.beta.messages, "create", create)
    counts = a.prewarm()
    real = a._request()
    assert seen["max_tokens"] == 0 and "cache_control" not in seen
    assert seen["system"] == real["system"] and seen["tools"] == real["tools"]
    assert seen.get("output_config") == real.get("output_config") and seen["model"] == real["model"]
    assert counts["cache_creation_input_tokens"] == 25000


def test_vorwaermen_fehler_bricht_nichts(monkeypatch):
    a = A.Agent(channel="web", conversation_id="warm-fehler")

    def create(**kw):
        raise api_error(anthropic.BadRequestError, 400, "Your credit balance is too low to access the Anthropic API.")

    monkeypatch.setattr(a.client.beta.messages, "create", create)
    assert a.prewarm() is None


def test_google_abgelaufen_klare_meldung(monkeypatch):
    import json

    from google.auth.exceptions import RefreshError
    from google.oauth2.credentials import Credentials

    from jarvis import config
    from jarvis.tools import ToolFailure
    from jarvis.tools import google as G

    G.token_path().write_text(json.dumps({"token": "t", "refresh_token": "r", "client_id": "c", "client_secret": "s",
                                          "token_uri": "https://oauth2.googleapis.com/token", "scopes": G.SCOPES,
                                          "expiry": "2020-01-01T00:00:00Z"}), encoding="utf-8")

    def refresh(self, request):
        raise RefreshError("invalid_grant: Token has been expired or revoked.")
    monkeypatch.setattr(Credentials, "refresh", refresh)
    try:
        with pytest.raises(ToolFailure, match="abgelaufen oder wurde widerrufen"):
            G._service("calendar", "v3")
    finally:
        G.token_path().unlink()
    assert config.data_dir()
