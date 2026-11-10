import json
import threading
import urllib.error
import urllib.request

import pytest

from jarvis import webui


class FakeAgent:
    model = "claude-sonnet-5-5"

    def __init__(self):
        self.cancelled = False

    def ask(self, text, on_sentence, on_tool):
        on_tool("nachrichten")
        on_sentence("Antwort eins.")
        on_sentence("Antwort zwei.")

    def reset(self):
        pass

    def cancel(self):
        self.cancelled = True


@pytest.fixture
def server(monkeypatch):
    srv = webui.ThreadingHTTPServer(("127.0.0.1", 0), webui.Handler)
    port = srv.server_address[1]
    monkeypatch.setattr(webui, "ALLOWED_HOSTS", {f"127.0.0.1:{port}", f"localhost:{port}"})
    fake = FakeAgent()
    monkeypatch.setattr(webui, "_agent", fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}", fake
    srv.shutdown()


def post(url, obj, headers=None):
    h = {"Content-Type": "application/json", "X-Jarvis": "1"}
    h.update(headers or {})
    return urllib.request.urlopen(urllib.request.Request(url, data=json.dumps(obj).encode(), headers=h, method="POST"),
                                  timeout=10)


def test_seite_und_stream(server):
    base, _ = server
    # Ein Host: die alte Seite leitet auf das Command Center (8080) um, ohne ihm zu folgen.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    with pytest.raises(urllib.error.HTTPError) as moved:
        urllib.request.build_opener(NoRedirect).open(base + "/")
    assert moved.value.code == 302 and moved.value.headers["Location"] == webui.DASHBOARD_URL
    with post(base + "/api/ask", {"text": "Hallo"}) as r:
        events = [json.loads(line) for line in r]
    assert [e["typ"] for e in events] == ["job", "werkzeug", "satz", "satz", "fertig"]


@pytest.mark.parametrize("headers", [{"X-Jarvis": ""}, {"Host": "evil.com:8765"}])
def test_fremde_anfragen_blockiert(server, headers):
    base, _ = server
    with pytest.raises(urllib.error.HTTPError) as e:
        post(base + "/api/reset", {}, headers)
    assert e.value.code == 403


def test_stop_und_einstellungen(server):
    base, fake = server
    post(base + "/api/stop", {})
    assert fake.cancelled
    with pytest.raises(urllib.error.HTTPError) as e:
        post(base + "/api/settings", {"stimme": "boese"})
    assert e.value.code == 400
    st = json.load(post(base + "/api/settings", {"stimme": "edge:de-DE-KatjaNeural", "tempo": 1.2}))
    assert st["stimme"] == "edge:de-DE-KatjaNeural" and st["tempo"] == 1.2


def test_audit_endpunkt(server):
    base, _ = server
    data = json.load(urllib.request.urlopen(base + "/api/audit"))
    assert "eintraege" in data
