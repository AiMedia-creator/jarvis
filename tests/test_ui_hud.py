"""UI-Smoke- und Zustandstests fuer das Voice-Core-HUD (Playwright; wird uebersprungen, wenn nicht installiert)."""

import os
import threading

import pytest

pw = pytest.importorskip("playwright.sync_api")

from jarvis import approvals, webui  # noqa: E402
from jarvis.tools import Tool, ToolFailure, boolean, string  # noqa: E402

CHROMIUM = "/opt/pw-browsers/chromium" if os.path.exists("/opt/pw-browsers/chromium") else None
sent = []


def _send(an, betreff, text, bestaetigt_vom_nutzer):
    sent.append(an)
    return f"Mail an {an} gesendet."


MAIL = approvals.wrap(Tool("mail_senden", "Sendet", {"an": string("an"), "betreff": string("b"), "text": string("t"),
                                                    "bestaetigt_vom_nutzer": boolean("ok")}, _send,
                           ["an", "betreff", "text", "bestaetigt_vom_nutzer"]))


class FakeAgent:
    """Simuliert den echten Agenten inkl. Token-Stream, Schritten und Abbruch."""
    model = "claude-sonnet-5-5"
    notes = []
    mode = "ok"

    def __init__(self):
        self.cancelled = threading.Event()
        self._job = None

    def cancel(self):
        self.cancelled.set()

    def ask(self, text, on_sentence, on_tool, on_token=None, on_step=None, job=None):
        from jarvis import jobs
        from jarvis.agent import Cancelled

        self.cancelled.clear()
        self._job = job
        if self.mode == "fehler":
            raise RuntimeError("Error code: 401 invalid x-api-key")
        if "lange" in text:
            step = jobs.add_step(job, "telegram_neuigkeiten", {}, "lesen")
            on_step(dict(step))
            for i in range(40):
                if self.cancelled.wait(0.1):
                    jobs.finish_step(step, jobs.CANCELLED)
                    raise Cancelled()
                on_token(f"Wort{i} ")
            return "x"
        if "Sätze" in text:
            for i in range(6):
                on_sentence(f"Satz Nummer {i}.")
            return "ok"
        if "Termine" in text and on_token:
            step = jobs.add_step(job, "kalender_termine", {}, "lesen")
            on_step(dict(step))
            jobs.finish_step(step, jobs.COMPLETED, "2 Termine")
            on_step(dict(step))
            for t in ["Du hast ", "heute ", "zwei Termine."]:
                on_token(t)
            on_sentence("Du hast heute zwei Termine.")
            return "ok"
        if "Mail" in text:
            on_tool("mail_senden")
            MAIL.run({"an": "max@muster.de", "betreff": "Angebot", "text": "Hallo Max", "bestaetigt_vom_nutzer": False})
            on_sentence("Die Mail liegt als Vorlage bereit. Soll ich sie senden?")
        else:
            on_tool("kalender_termine")
            on_sentence("Du hast heute zwei Termine.")

    def reset(self):
        pass


@pytest.fixture(scope="module")
def page():
    srv = webui.ThreadingHTTPServer(("127.0.0.1", 0), webui.Handler)
    port = srv.server_address[1]
    webui.ALLOWED_HOSTS = {f"127.0.0.1:{port}", f"localhost:{port}"}
    webui._agent = FakeAgent()
    webui.overview = lambda: {"datum": "Samstag, 17. Oktober 2026", "google": False, "hubspot": False, "news": []}
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    with pw.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROMIUM)
        pg = browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.base = f"http://localhost:{port}"
        pg.errors = errors
        yield pg
        browser.close()
    srv.shutdown()


@pytest.fixture(autouse=True)
def _keine_alten_vorlagen():
    approvals._items.clear()  # andere Tests duerfen keine offenen Freigaben hinterlassen
    yield


STATES = ["IDLE", "LISTENING", "TRANSCRIBING", "THINKING", "SPEAKING", "INTERRUPTED", "WAITING_FOR_APPROVAL", "ERROR"]
LABELS = {"IDLE": "Bereit", "LISTENING": "Ich höre zu", "TRANSCRIBING": "Du sprichst", "THINKING": "Verarbeite Auftrag",
          "SPEAKING": "Spreche", "INTERRUPTED": "Unterbrochen", "WAITING_FOR_APPROVAL": "Wartet auf Freigabe", "ERROR": "Fehler"}


def test_mock_alle_zustaende_unterscheidbar(page):
    page.goto(page.base + "/?mock=1")
    page.wait_for_timeout(600)
    for s in STATES:
        page.evaluate(f"jarvisHUD.setMock('{s}')")
        page.wait_for_timeout(150)
        assert page.evaluate("jarvisHUD.state") == s
        assert page.get_attribute("#coreState", "data-s") == s
        assert page.text_content("#coreLabel") == LABELS[s]
    assert page.locator(".mockbar button").count() == 9
    assert not page.errors


def test_mock_pegel_reagiert(page):
    page.goto(page.base + "/?mock=1")
    page.evaluate("jarvisHUD.setMock('LISTENING')")
    page.wait_for_timeout(1200)
    peak = max(page.evaluate("jarvisHUD.ctrl.smoothed") for _ in range(10) if not page.wait_for_timeout(60))
    assert peak > 0.02
    page.evaluate("jarvisHUD.setMock('IDLE')")
    page.wait_for_timeout(1500)
    assert page.evaluate("jarvisHUD.ctrl.smoothed") < 0.05


def test_reduzierte_animationen_und_stop(page):
    page.goto(page.base + "/?mock=1")
    page.click("#settingsBtn")
    page.check("#reducedChk")
    page.keyboard.press("Escape")
    assert page.evaluate("document.body.classList.contains('reduced') && jarvisHUD.ctrl.reducedMotion")
    page.evaluate("jarvisHUD.setMock('SPEAKING')")
    assert page.text_content("#coreLabel") == "Spreche"  # Status bleibt sichtbar
    page.click("#stopBtn")
    assert page.evaluate("jarvisHUD.state") == "IDLE"
    page.evaluate("localStorage.clear()")


def test_kern_klick_startet_nur_sitzung(page):
    requests = []
    page.on("request", lambda r: requests.append(r.url))
    page.goto(page.base + "/?mock=1")
    page.click("#core")
    assert page.evaluate("jarvisHUD.state") == "LISTENING"
    page.click("#core")
    assert page.evaluate("jarvisHUD.state") == "IDLE"
    assert not any("/api/approvals/approve" in u or "/api/ask" in u for u in requests)


def test_vorlage_freigabe_ablauf(page):
    approvals._items.clear()
    sent.clear()
    page.goto(page.base + "/")
    page.evaluate("voiceOn=false")
    page.fill("#input", "Schreib Max eine Mail")
    page.keyboard.press("Enter")
    page.wait_for_selector(".approval", timeout=5000)
    page.wait_for_timeout(400)
    assert page.evaluate("jarvisHUD.state") == "WAITING_FOR_APPROVAL"
    assert page.is_visible("#approvalBanner") and sent == []
    page.click("#approvalShow")
    page.click(".approval .btn.primary")
    page.wait_for_timeout(800)
    assert sent == ["max@muster.de"]
    assert page.evaluate("jarvisHUD.state") == "IDLE"
    assert "erledigt" in page.inner_text(".approval .st")


def test_fehler_zustand_mit_wiederholen(page):
    webui._agent.mode = "fehler"
    page.goto(page.base + "/")
    page.evaluate("voiceOn=false")
    page.fill("#input", "Was steht heute an?")
    page.keyboard.press("Enter")
    page.wait_for_timeout(800)
    assert page.evaluate("jarvisHUD.state") == "ERROR"
    assert "Schlüssel" in page.inner_text("#errorText")
    webui._agent.mode = "ok"
    page.click("#errorRetry")
    page.wait_for_timeout(800)
    assert page.evaluate("jarvisHUD.state") == "IDLE"
    assert "zwei Termine" in page.inner_text("#chat")


def test_handy_ansicht_ohne_horizontales_scrollen(page):
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(page.base + "/?mock=1")
    page.wait_for_timeout(400)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    assert page.evaluate("document.querySelector('#core').getBoundingClientRect().width") <= 320
    page.set_viewport_size({"width": 1440, "height": 900})
    assert not page.errors


def test_token_stream_schritte_und_messwerte(page):
    page.goto(page.base + "/")
    page.evaluate("voiceOn=false")
    page.fill("#input", "Welche Termine habe ich?")
    page.keyboard.press("Enter")
    page.wait_for_timeout(700)
    assert "Du hast heute zwei Termine." in page.inner_text("#chat")
    jobs_text = page.inner_text("#jobs")
    assert "Erledigt" in jobs_text and "Kalender" in jobs_text and "erste Worte" in jobs_text
    assert page.evaluate("jarvisHUD.state") == "IDLE"


def test_neue_eingabe_und_stopp_brechen_wirklich_ab(page):
    page.goto(page.base + "/")
    page.evaluate("voiceOn=false")
    page.fill("#input", "Erzähl mir eine lange Geschichte")
    page.keyboard.press("Enter")
    page.wait_for_timeout(500)
    assert page.evaluate("jarvisHUD.state") == "THINKING"
    page.fill("#input", "Welche Termine habe ich?")      # neue Eingabe waehrend er antwortet
    page.keyboard.press("Enter")
    page.wait_for_timeout(900)
    assert webui._agent.cancelled.is_set() or page.locator(".msg.cut").count() >= 1
    assert page.locator(".msg.cut").count() >= 1
    assert "zwei Termine" in page.inner_text("#chat")
    # Stopp waehrend einer laufenden Antwort: Server-Abbruch + Zustand UNTERBROCHEN, danach BEREIT
    page.fill("#input", "Noch eine lange Antwort")
    page.keyboard.press("Enter")
    page.wait_for_timeout(400)
    page.click("#stopBtn")
    page.wait_for_timeout(150)
    assert page.evaluate("jarvisHUD.state") == "INTERRUPTED"
    page.wait_for_timeout(1200)
    assert webui._agent.cancelled.is_set()
    assert page.evaluate("jarvisHUD.state") == "IDLE"
    assert "Abgebrochen" in page.inner_text("#jobs")
    assert not page.errors


def test_hoechstens_zwei_sprachausgaben_gleichzeitig(page, monkeypatch):
    """Schutz gegen das Aufhaengen: nie mehr als 2 parallele Sprachausgabe-Anfragen (Browser hat nur 6 Verbindungen)."""
    import time as _t

    from jarvis import tts

    state = {"now": 0, "max": 0}
    lock = threading.Lock()

    def slow(text, voice=None):
        with lock:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        _t.sleep(0.4)
        with lock:
            state["now"] -= 1
        return b"ID3"

    monkeypatch.setattr(tts, "synthesize", slow)
    page.goto(page.base + "/")
    page.evaluate("voiceOn=true")
    page.fill("#input", "Sag mehrere Sätze")
    page.keyboard.press("Enter")
    page.wait_for_timeout(3500)
    assert state["max"] <= 2, state
    assert page.evaluate("jarvisHUD.state") == "IDLE"   # haengt nicht, wird wieder bereit


def test_smartes_unterbrechen(page, monkeypatch):
    """Stimmen im Raum unterbrechen nicht; 'Stopp', 'Jarvis …' und relevante Saetze schon."""
    from jarvis import bargein

    monkeypatch.setattr(bargein, "should_interrupt",
                        lambda text, ctx="": {"unterbrechen": "Meier" in text, "grund": "test"})
    page.goto(page.base + "/")
    page.evaluate("voiceOn=false; localStorage.setItem('jarvis.barge', JSON.stringify('smart'))")

    def start_long():
        page.fill("#input", "Erzähl mir eine lange Geschichte")
        page.keyboard.press("Enter")
        page.wait_for_timeout(400)
        assert page.evaluate("jarvisHUD.state") == "THINKING"

    start_long()
    page.evaluate("jarvisHUD.heard('Schatz, hast du die Schlüssel irgendwo gesehen')")   # Gespraech im Raum
    page.evaluate("jarvisHUD.heard('mhm')")
    page.evaluate("jarvisHUD.heard('Ich glaube das Paket kommt morgen', 0.05)")          # leise = Hintergrund
    page.wait_for_timeout(300)
    assert page.evaluate("jarvisHUD.state") == "THINKING" and page.locator(".msg.cut").count() == 0
    assert page.evaluate("jarvisHUD.ignored") >= 3

    page.evaluate("jarvisHUD.heard('Nein, schreib lieber an Herrn Meier')")             # relevant -> unterbrechen
    page.wait_for_timeout(600)
    assert page.locator(".msg.cut").count() == 1
    assert "schreib lieber an Herrn Meier" in page.inner_text("#chat")
    page.wait_for_timeout(4500)                                                          # lange Antwort laeuft aus

    start_long()
    page.evaluate("jarvisHUD.heard('Stopp')")
    page.wait_for_timeout(300)
    assert page.locator(".msg.cut").count() == 2
    page.wait_for_timeout(1200)
    assert page.evaluate("jarvisHUD.state") == "IDLE"
    page.evaluate("localStorage.clear()")
