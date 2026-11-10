"""Oberflaeche: fremde Ereignisse verwerfen, Hintergrundjob neben dem Gespraech, Stopp im ElevenLabs-Modus."""

import os
import threading
import time

import pytest

pw = pytest.importorskip("playwright.sync_api")

from helpers import make_agent  # noqa: E402
from jarvis import approvals, eleven_agent, jobs, runtime, webui  # noqa: E402

CHROMIUM = "/opt/pw-browsers/chromium" if os.path.exists("/opt/pw-browsers/chromium") else None

FAKE_SDK = """
window.__fake = {vol: null, ended: false, opts: null, sent: []};
window.ElevenLabsClient = { Conversation: { startSession: async (opts) => {
  window.__fake.opts = opts;
  setTimeout(() => opts.onConnect && opts.onConnect({conversationId: "c1"}), 10);
  return {
    setVolume: ({volume}) => { window.__fake.vol = volume; },
    endSession: async () => { window.__fake.ended = true; opts.onDisconnect && opts.onDisconnect({reason: "user"}); },
    getInputVolume: () => 0.2, getOutputVolume: () => 0.6,
    sendUserMessage: (t) => window.__fake.sent.push(t), sendContextualUpdate: () => {},
  };
}}};
"""


@pytest.fixture(scope="module")
def page():
    webui._agent = make_agent()
    webui.make_agent = lambda channel, conv: make_agent(channel, conv)
    runtime.set_factory(webui._factory)
    srv = webui.ThreadingHTTPServer(("127.0.0.1", 0), webui.Handler)
    port = srv.server_address[1]
    webui.ALLOWED_HOSTS = {f"127.0.0.1:{port}", f"localhost:{port}"}
    webui.overview = lambda: {"datum": "Samstag", "google": False, "hubspot": False, "news": []}
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    with pw.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROMIUM)
        pg = browser.new_page(viewport={"width": 1440, "height": 900})
        pg.errors = []
        pg.on("pageerror", lambda e: pg.errors.append(str(e)))
        pg.base = f"http://localhost:{port}"
        yield pg
        browser.close()
    srv.shutdown()


@pytest.fixture(autouse=True)
def _sauber():
    approvals._items.clear()
    yield


# 3) Ereignisse mit falscher jobId werden verworfen
def test_falsche_job_id_wird_verworfen(page):
    page.goto(page.base + "/")
    res = page.evaluate("""() => {
      const job = {id: null, conv: null};
      const r = [];
      r.push(jarvisHUD.belongs({typ: "token", job_id: "fremd", conversation_id: "haupt"}, job)); // vor job-Event
      r.push(jarvisHUD.belongs({typ: "job", job_id: "j1", conversation_id: "haupt"}, job));
      r.push(jarvisHUD.belongs({typ: "token", job_id: "j1", conversation_id: "haupt"}, job));
      r.push(jarvisHUD.belongs({typ: "token", job_id: "j2", conversation_id: "haupt"}, job));
      r.push(jarvisHUD.belongs({typ: "token", job_id: "j1", conversation_id: "bg-x"}, job));
      return r; }""")
    assert res == [False, True, True, False, False]


# 6) Hintergrundjob waehrend aktiver Unterhaltung – nichts davon landet im Gespraech
def test_hintergrundjob_neben_gespraech(page):
    page.goto(page.base + "/")
    page.evaluate("voiceOn=false")
    page.fill("#input", "Analysiere langsam Hintergrundzeug")
    page.click("#bgBtn")
    page.wait_for_timeout(300)
    assert page.is_visible("#jobsPane")
    page.click(".tabs button[data-pane=chat]")
    page.fill("#input", "Was macht HubSpot?")
    page.keyboard.press("Enter")
    page.wait_for_timeout(800)
    chat = page.inner_text("#chat")
    assert "Antwort zu HubSpot." in chat and "Hintergrundzeug-" not in chat
    assert page.evaluate("jarvisHUD.state") == "IDLE"
    page.click(".tabs button[data-pane=jobsPane]")
    page.wait_for_timeout(300)
    assert "Hintergrund" in page.inner_text("#jobList")
    page.wait_for_timeout(2400)       # Hintergrundjob fertig
    page.click(".tabs button[data-pane=jobsPane]")
    page.wait_for_timeout(500)
    first = page.locator(".jrow").filter(has_text="Analysiere")
    assert "Erledigt" in first.inner_text()
    assert "Hintergrundzeug-" not in page.inner_text("#chat")
    assert "Erstes Token" in page.text_content("#perf")
    assert not page.errors


# 5) Unterbrechung waehrend ElevenLabs-Audio
def test_stopp_waehrend_elevenlabs_audio(page, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test")
    monkeypatch.setattr(eleven_agent, "prepare_session",
                        lambda pause=1.0: {"signed_url": "wss://fake", "agent_id": "a1", "werkzeuge": ["merken"]})
    page.route("**/vendor/elevenlabs-client.js", lambda r: r.fulfill(status=200, content_type="text/javascript", body=FAKE_SDK))
    page.goto(page.base + "/")
    page.evaluate("localStorage.setItem('jarvis.core', JSON.stringify('auto'))")
    page.reload()
    page.wait_for_timeout(600)
    page.click("#talkBtn")
    page.wait_for_timeout(400)
    page.evaluate("__fake.opts.onMessage({role: 'user', message: 'Merk dir, dass Max vormittags erreichbar ist'})")
    page.wait_for_timeout(300)
    out = page.evaluate("__fake.opts.clientTools.merken({notiz: 'Max vormittags erreichbar'})")
    assert out == "Gemerkt."
    page.evaluate("__fake.opts.onModeChange({mode: 'speaking'})")
    page.wait_for_timeout(300)
    assert page.evaluate("jarvisHUD.state") == "SPEAKING"
    jobs_text = page.inner_text("#jobs")
    assert "Merk dir" in jobs_text and "merkt sich etwas" in jobs_text   # Job + Schritt sichtbar waehrend er spricht
    job_id = page.evaluate("jarvisHUD.jobView.id")
    page.click("#stopBtn")
    page.wait_for_timeout(400)
    assert page.evaluate("__fake.vol") == 0 and page.evaluate("__fake.ended") is True
    assert jobs.get(job_id)["status"] == jobs.CANCELLED
    assert page.evaluate("jarvisHUD.state") in ("INTERRUPTED", "IDLE")
    page.unroute("**/vendor/elevenlabs-client.js")
    page.evaluate("localStorage.clear()")
    assert not page.errors
