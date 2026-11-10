"""Kein stiller Abbruch: bekannte Claude-Ausfälle werden gesagt; Meldungen ohne Textstrom erreichen den Browser."""

import json
import types
import uuid

import anthropic
import httpx
import pytest
from aiohttp.test_utils import TestClient, TestServer

from jarvis import agent as A
from jarvis import v3_bridge

B = types.SimpleNamespace


def api_error(cls, status, message):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status, request=request, json={"error": {"type": "x", "message": message}})
    return cls(message=message, response=response, body=None)


@pytest.mark.parametrize("exc, part", [
    (api_error(anthropic.BadRequestError, 400, "Your credit balance is too low to access the Anthropic API."), "Guthaben"),
    (api_error(anthropic.AuthenticationError, 401, "invalid x-api-key"), "Schlüssel"),
    (api_error(anthropic.RateLimitError, 429, "rate limited"), "bremst"),
    (api_error(anthropic.InternalServerError, 529, "Overloaded"), "überlastet"),
    (anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com")), "erreiche Claude"),
])
def test_bekannte_ausfaelle_werden_klar_benannt(exc, part):
    assert part in A.spoken_error(exc)


def test_unbekannter_fehler_bleibt_fehler():
    assert A.spoken_error(ValueError("kaputt")) is None


def test_agent_sagt_guthaben_problem_statt_abzubrechen():
    a = A.Agent(channel="web", conversation_id="guthaben-test")

    def broken(**kw):
        raise api_error(anthropic.BadRequestError, 400, "Your credit balance is too low to access the Anthropic API.")
    a.client = B(beta=B(messages=B(stream=broken)))
    said, tokens = [], []
    answer = a.ask("Hallo", on_sentence=said.append, on_token=tokens.append)
    assert "Guthaben" in answer and said == [answer] and tokens == [answer]


TOKEN = "offline-unit-test-bridge-credential-only"


@pytest.mark.asyncio
async def test_bruecke_sendet_antwort_ohne_textstrom_trotzdem(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))

    class QuietAgent:  # antwortet nur über den Rückgabewert (z. B. Budget- oder Zeitlimit-Meldung)
        conversation_id = "room-quiet"

        def ask(self, text, **kw):
            return "Ich habe hier abgebrochen: Das Zeitlimit für diesen Auftrag ist erreicht."

        def cancel(self):
            pass

    app = v3_bridge.create_app(TOKEN, demo=True)
    bridge = next(v for v in app.values() if isinstance(v, v3_bridge.Bridge))
    monkeypatch.setattr(bridge, "current_agent", lambda conversation_id: QuietAgent())
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        r = await client.post("/internal/generate", json={"text": "Hi", "conversation_id": "room-quiet",
                                                           "request_id": str(uuid.uuid4())},
                              headers={"Authorization": "Bearer " + TOKEN})
        events = [json.loads(line) for line in (await r.text()).splitlines() if line.strip().startswith("{")]
        assert "Zeitlimit" in "".join(e.get("text", "") for e in events if e.get("type") == "token")
        assert events[-1]["type"] == "done"
    finally:
        await client.close()
