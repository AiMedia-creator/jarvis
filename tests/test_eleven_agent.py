import pytest

from jarvis import eleven_agent as E


@pytest.fixture
def el(mock_server, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test")
    counter = {"n": 0}

    def respond(method, path, body):
        if path == "/v1/convai/tools" and method == "POST":
            counter["n"] += 1
            return 200, {"id": f"tool_{counter['n']}"}
        if path.startswith("/v1/convai/tools/"):
            return 200, {"id": path.rsplit("/", 1)[1]}
        if path == "/v1/convai/agents/create":
            return 200, {"agent_id": "agent_1"}
        if path.startswith("/v1/convai/agents/"):
            return 200, {}
        if path.startswith("/v1/convai/conversation/get-signed-url"):
            return 200, {"signed_url": "wss://x?token=1"}
        return 404, {"detail": "nope"}

    url, log = mock_server(respond)
    monkeypatch.setattr(E, "API", url + "/v1")
    return log


def test_agent_anlegen_und_wiederverwenden(el):
    s = E.prepare_session(1.0)
    assert s["agent_id"] == "agent_1" and s["signed_url"].startswith("wss://")
    create = next(e for e in el if e["path"] == "/v1/convai/agents/create")["body"]
    cc = create["conversation_config"]
    assert cc["agent"]["language"] == "de" and cc["agent"]["prompt"]["llm"] == "claude-sonnet-5-5"
    assert create["platform_settings"]["auth"]["enable_auth"] is True
    assert "@@MEMORY@@" not in cc["agent"]["prompt"]["prompt"]
    n = len(el)
    E.prepare_session(2.0)
    zweiter = el[n:]
    assert not any("/convai/tools" in e["path"] for e in zweiter)  # Werkzeuge unveraendert -> kein Upload
    assert zweiter[0]["body"]["conversation_config"]["turn"]["turn_eagerness"] == "patient"


def test_run_tool_typen_und_audit(monkeypatch):
    from jarvis import audit

    out = E.run_tool("merken", {"notiz": "Testnotiz"})
    assert out == "Gemerkt."
    assert E.run_tool("gibtsnicht", {}).startswith("Fehler")
    assert audit.tail(5)[0]["werkzeug"] == "merken"
