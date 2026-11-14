"""Durchstich mit dem echten Browser Use 1.0.2, dem echten Wegwerf-Chrome und beiden Sandboxen – gegen eine LOKALE
Testseite in diesem Prozess. Das Modell ist ein Skript (Pydantic AI ``FunctionModel``): keine API-Aufrufe, keine
Kosten, kein Konto, keine Außenwirkung. Wird übersprungen, wenn Chrome, Sandbox oder das browser-use-venv fehlen.
"""

import asyncio
import datetime
import json
import os
import ssl
import threading
from pathlib import Path

import pytest

from jarvis import approvals, browser_agent as b, browser_netz as n

VENV = Path("/Users") / os.environ.get("JARVIS_TEST_ECHTER_NUTZER", "") / ".jarvis/venvs/browser-use/bin/python"
HOST = "shop.jarvis-test.example"

pytestmark = pytest.mark.skipif(
    not (os.environ.get("JARVIS_TEST_ECHTER_NUTZER") and VENV.exists() and b.chrome() and b.sandbox_moeglich()),
    reason="braucht JARVIS_TEST_ECHTER_NUTZER=<name>, das browser-use-venv, Chrome und sandbox-exec")

SEITEN = {
    "/": "<title>Testladen</title><h1>Testladen</h1><a id=kontakt href=/kontakt>Kontakt</a>",
    "/kontakt": ("<title>Kontakt</title><form method=post action=/senden><input name=name><textarea name=nachricht></textarea>"
                 "<button type=submit>Absenden</button></form>"),
}

START = r'''
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import runner
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

ZELLEN = __ZELLEN__

def modell(messages, info):
    n = sum(1 for m in messages if isinstance(m, ModelResponse))
    if n < len(ZELLEN):
        return ModelResponse(parts=[ToolCallPart("python", {"code": ZELLEN[n]})])
    ziel = next(t.name for t in info.output_tools if "finish_from_repl" in t.name)
    return ModelResponse(parts=[ToolCallPart(ziel, {"expression": "json.dumps(BERICHT, ensure_ascii=False)"})])

try:
    auftrag = runner.lies_auftrag()
    raise SystemExit(runner.laufen(runner.baue_agent(auftrag, FunctionModel(modell)), auftrag))
except SystemExit:
    raise
except BaseException as exc:
    runner.melde_fehler(exc)
    raise SystemExit(1)
'''

HELFER = r'''
import json, time
BERICHT = {}
def mitte(sel):
    return js("(()=>{const b=document.querySelector(%s).getBoundingClientRect();return [b.x+b.width/2,b.y+b.height/2]})()" % json.dumps(sel))
def probier(name, f):
    try:
        BERICHT[name] = "OFFEN: " + str(f())[:120]
    except BaseException as e:
        BERICHT[name] = "gesperrt: " + type(e).__name__
def im_browser(ausdruck):
    return cdp("Runtime.evaluate", expression=ausdruck, awaitPromise=True, returnByValue=True)["result"].get("value")
'''

ZELLEN = [
    HELFER + r'''
new_tab("https://__HOST__/")
wait_for_load()
BERICHT["titel"] = js("document.title")
x, y = mitte("a#kontakt"); click_at_xy(x, y)          # Link anklicken
time.sleep(1.0); wait_for_load()
BERICHT["nach_klick"] = page_info()["url"]
''',
    r'''
x, y = mitte("input[name=name]"); click_at_xy(x, y); type_text("Erika Beispiel")        # Text eingeben
x, y = mitte("textarea[name=nachricht]"); click_at_xy(x, y); type_text("Bitte um Rückruf")
BERICHT["eingegeben"] = js("[document.querySelector('input[name=name]').value, document.querySelector('textarea[name=nachricht]').value]")
''',
    r'''
x, y = mitte("button[type=submit]"); click_at_xy(x, y)  # Absenden auslösen: muss festgehalten werden
time.sleep(2.0)
BERICHT["nach_absenden"] = im_browser("document.body ? document.body.innerText : ''")
''',
    r'''
import os, socket, urllib.request
# Ausbruchsversuche aus dem modellgeschriebenen Code – alle müssen scheitern
probier("code_internet", lambda: urllib.request.urlopen("https://example.com", timeout=5).status)
probier("code_dns", lambda: socket.getaddrinfo("example.com", 443))
probier("code_jarvis_8080", lambda: socket.create_connection(("127.0.0.1", 8080), 3))
probier("code_bruecke_8766", lambda: socket.create_connection(("127.0.0.1", 8766), 3))
probier("code_proxy_direkt", lambda: socket.create_connection(("127.0.0.1", __PROXY__), 3))
probier("code_testseite_direkt", lambda: socket.create_connection(("127.0.0.1", __SEITE__), 3))
probier("code_heim_lesen", lambda: os.listdir("__HEIM__"))
probier("code_heim_datei", lambda: open("__HEIM__/.zshrc").read())
probier("code_heim_schreiben", lambda: open("__HEIM__/jarvis-sandbox-probe.txt", "w").write("x"))
probier("code_http_get", lambda: http_get("https://__HOST__/"))
BERICHT["umgebung_schluessel"] = sorted(k for k in os.environ if any(w in k.upper() for w in ("KEY", "TOKEN", "SECRET", "PASSW")))
''',
    r'''
# Ausbruchsversuche über den Browser – der Filterproxy muss sie sperren
def laden(url):
    return im_browser("fetch(%s, {mode: 'no-cors'}).then(r => 'OFFEN ' + r.status + ' ' + r.type).catch(e => 'gesperrt')" % json.dumps(url))
for name, url in {"browser_loopback": "http://127.0.0.1:__SEITE__/", "browser_jarvis": "http://localhost:8080/health",
                  "browser_fremde_domain": "https://boese.jarvis-test.example/", "browser_metadaten": "http://169.254.169.254/latest/meta-data/"}.items():
    BERICHT[name] = laden(url)
BERICHT["browser_post_fetch"] = im_browser("fetch('/senden', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({heimlich: 'ja'})}).then(r => r.status).catch(e => 'gesperrt')")
probier("browser_datei", lambda: (goto_url("file:///etc/passwd"), page_info()["url"])[1])
''',
]


def _tls(tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOST)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(1)
            .not_valid_before(now - datetime.timedelta(minutes=1)).not_valid_after(now + datetime.timedelta(hours=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(HOST)]), critical=False).sign(key, hashes.SHA256()))
    (tmp_path / "c.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (tmp_path / "k.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(str(tmp_path / "c.pem"), str(tmp_path / "k.pem"))
    return server, ssl.create_default_context(cafile=str(tmp_path / "c.pem"))


class Seite:
    """Lokale HTTPS-Seite in einem eigenen Thread; zeichnet jede Anfrage auf."""

    def __init__(self, tls):
        self.anfragen, self._tls = [], tls
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        self.port = asyncio.run_coroutine_threadsafe(self._start(), self._loop).result(10)
        return self

    def __exit__(self, *exc):
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(5)

    async def _start(self):
        server = await asyncio.start_server(self._handle, "127.0.0.1", 0, ssl=self._tls)
        return server.sockets[0].getsockname()[1]

    async def _handle(self, r, w):
        try:
            kopf = (await r.readuntil(b"\r\n\r\n")).decode("latin-1")
            felder = {k.strip().lower(): v.strip() for k, _, v in (z.partition(":") for z in kopf.split("\r\n")[1:] if z)}
            rumpf = await r.readexactly(int(felder.get("content-length", 0)))
            methode, pfad = kopf.split(" ")[0], kopf.split(" ")[1]
            self.anfragen.append((methode, pfad, rumpf))
            text = (SEITEN.get(pfad, "<title>404</title>") if methode == "GET" else "<title>Danke</title>Nachricht gespeichert").encode()
            w.write(f"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {len(text)}\r\n\r\n".encode() + text)
            await w.drain()
        except Exception:
            pass
        finally:
            w.close()


@pytest.fixture
def echt(monkeypatch):
    monkeypatch.setenv("JARVIS_BROWSER_AGENT", "an")
    monkeypatch.setenv("JARVIS_BROWSER_INTERAKTION", "an")
    monkeypatch.setenv("JARVIS_BROWSER_AGENT_PYTHON", str(VENV))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-darf-nie-im-agenten-auftauchen")


def _lauf(tmp_path, seite, vertraut, modus, monkeypatch):
    ports = {}
    echtes_starten = b._starten

    def starten(cmd, eingabe, env, cwd, zeitlimit):
        auftrag = json.loads(eingabe)
        ports["eingabe"] = eingabe
        zellen = json.dumps(ZELLEN).replace("__HOST__", HOST).replace("__SEITE__", str(seite.port)).replace(
            "__PROXY__", str(ports["proxy"])).replace("__HEIM__", str(VENV.parents[4]))
        (Path(cwd) / "start.py").write_text(START.replace("__ZELLEN__", zellen), encoding="utf-8")
        assert auftrag["cdp_url"].startswith("http://127.0.0.1:") and "schluessel" not in auftrag
        return echtes_starten(cmd, eingabe, env, cwd, zeitlimit)

    echtes_chrome = b._chrome_starten

    def chrome_starten(arbeit, proxy_port, cdp_port, spki):
        ports["proxy"] = proxy_port
        ports["arbeit"] = arbeit
        return echtes_chrome(arbeit, proxy_port, cdp_port, spki)

    monkeypatch.setattr(b, "_starten", starten)
    monkeypatch.setattr(b, "_chrome_starten", chrome_starten)
    platzhalter = tmp_path / "start.py"
    platzhalter.write_text("", encoding="utf-8")
    regeln = n.Netzregeln([HOST], nur_test_ziele={f"{HOST}:443": ("127.0.0.1", seite.port)})
    ergebnis = b.ausfuehren("Öffne die Kontaktseite, trage Name und Nachricht ein und sende das Formular ab.", [HOST], modus,
                            _regeln=regeln, _upstream_ssl=vertraut, _runner=platzhalter)
    return ergebnis, ports


def test_interaktion_klickt_tippt_und_haelt_das_absenden_fest(tmp_path, echt, monkeypatch):
    server_tls, vertraut = _tls(tmp_path)
    with Seite(server_tls) as seite:
        ergebnis, ports = _lauf(tmp_path, seite, vertraut, "interaktion", monkeypatch)
        bericht = json.loads(ergebnis["ergebnis"])
        # 1. Klick und Eingabe haben über die öffentlichen Helfer von Browser Use funktioniert
        assert bericht["titel"] == "Testladen" and bericht["nach_klick"] == f"https://{HOST}/kontakt"
        assert bericht["eingegeben"] == ["Erika Beispiel", "Bitte um Rückruf"]
        # 2. Das Absenden kam NICHT bei der Website an, sondern wurde festgehalten
        assert [a for a in seite.anfragen if a[0] != "GET"] == []
        assert ("GET", "/") in [a[:2] for a in seite.anfragen] and ("GET", "/kontakt") in [a[:2] for a in seite.anfragen]
        assert "zurückgehalten" in bericht["nach_absenden"]
        assert bericht["browser_post_fetch"] == 403
        aktionen = ergebnis["aktionen"]
        assert [a["aktion"] for a in aktionen] == ["Formular absenden", "Daten senden"] and all(a["website"] == HOST for a in aktionen)
        # 3. Ausbruchsversuche: Code ohne Netz und ohne Benutzerdateien; Browser nur zu erlaubten Zielen
        offen = {k: v for k, v in bericht.items() if k.startswith("code_") and not str(v).startswith("gesperrt")}
        assert offen == {}, offen
        # Über den Browser: https-Ziele scheitern schon am Tunnel; bei http antwortet der Filterproxy selbst mit seiner
        # Sperrseite (für die Seite eine undurchsichtige Antwort). Entscheidend: nichts davon erreichte ein Ziel.
        assert bericht["browser_fremde_domain"] == "gesperrt" and str(bericht["browser_datei"]).startswith("gesperrt")
        gesperrt = {g["ziel"]: g["grund"] for g in ergebnis["gesperrt"]}
        assert "IP-Adressen" in gesperrt[f"http://127.0.0.1:{seite.port}/"] and "IP-Adressen" in gesperrt["http://169.254.169.254/latest/meta-data/"]
        assert "Domain-Liste" in gesperrt["http://localhost:8080/health"] and "Domain-Liste" in gesperrt["boese.jarvis-test.example:443"]
        assert [a[:2] for a in seite.anfragen].count(("GET", "/")) == 1  # nur der eine gewollte Aufruf, keiner über Loopback
        assert bericht["umgebung_schluessel"] == [] and "sk-test-darf-nie" not in ports["eingabe"]
        assert not (VENV.parents[4] / "jarvis-sandbox-probe.txt").exists()
        # 4. Aufgeräumt: Wegwerfordner weg, kein Chrome dieses Laufs mehr
        assert not ports["arbeit"].exists()

        # 5. Gesonderte Freigabe: Karte zeigt Aktion, Website und Daten; erst danach geht GENAU diese Anfrage hinaus
        tool = next(approvals.wrap(t) for t in b.tools() if t.name == "browser_absenden")
        assert "VORLAGE" in tool.handler(aktion_id=aktionen[0]["id"])
        assert [a for a in seite.anfragen if a[0] != "GET"] == []
        karte = next(a for a in approvals.listing(50) if a["werkzeug"] == "browser_absenden" and a["status"] == "WAITING_FOR_APPROVAL")
        zeilen = {z["feld"]: z["wert"] for z in karte["vorschau"]["zeilen"]}
        assert zeilen["Aktion"] == "Formular absenden (POST)" and zeilen["Website"] == HOST and zeilen["Adresse"] == f"https://{HOST}/senden"
        assert zeilen["Zu sendende Daten"] == "name = Erika Beispiel\nnachricht = Bitte um Rückruf"
        antwort = approvals.approve(karte["id"])
        assert "Gesendet: Formular absenden" in antwort and "HTTP 200" in antwort
        gesendet = [a for a in seite.anfragen if a[0] != "GET"]
        assert gesendet == [("POST", "/senden", b"name=Erika+Beispiel&nachricht=Bitte+um+R%C3%BCckruf")]
        # 6. Einmal verwendbar; die zweite, nie freigegebene Aktion bleibt ungesendet
        with pytest.raises(Exception, match="gibt es nicht"):
            b.browser_absenden(aktionen[0]["id"], bestaetigt_vom_nutzer=True)
        assert len([a for a in seite.anfragen if a[0] != "GET"]) == 1


def test_recherche_sendet_nichts_und_haelt_nichts_fest(tmp_path, echt, monkeypatch):
    server_tls, vertraut = _tls(tmp_path)
    with Seite(server_tls) as seite:
        ergebnis, _ = _lauf(tmp_path, seite, vertraut, "lesen", monkeypatch)
        bericht = json.loads(ergebnis["ergebnis"])
        assert bericht["titel"] == "Testladen" and bericht["browser_post_fetch"] == 403
        assert [a for a in seite.anfragen if a[0] != "GET"] == [] and ergebnis["aktionen"] == []
        assert any("nur lesender Modus" in g["grund"] for g in ergebnis["gesperrt"])
