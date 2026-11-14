"""Netzgrenzen des Browser-Agenten: Filterproxy (Domains, Adressen, Methoden, HTTPS) und LLM-Relay.

Alle Gegenstellen sind lokale Testserver in diesem Prozess. Es gibt keinen Zugriff auf das Internet und kein echtes Konto.
"""

import asyncio
import base64
import datetime
import hashlib
import json
import ssl

import pytest

from jarvis import browser_netz as n


# --- Adressen und Regeln ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("ip", ["127.0.0.1", "127.8.9.1", "10.0.0.5", "172.16.3.4", "192.168.1.1", "169.254.169.254", "100.64.0.1",
                                "0.0.0.0", "224.0.0.1", "::1", "fe80::1", "fd00::1", "::ffff:127.0.0.1", "::ffff:10.0.0.1",
                                "64:ff9b::7f00:1", "2002:7f00:1::", "fd00:ec2::254", "kein-ip", ""])
def test_nicht_oeffentliche_adressen_sind_gesperrt(ip):
    assert n.ip_oeffentlich(ip) is False


@pytest.mark.parametrize("ip", ["93.184.216.34", "1.1.1.1", "2606:4700:4700::1111", "::ffff:93.184.216.34"])
def test_oeffentliche_adressen_sind_erlaubt(ip):
    assert n.ip_oeffentlich(ip) is True


def _regeln(antworten, domains=("muster.de",), **kw):
    async def aufloeser(host, port):
        return antworten.get(host, [])
    return n.Netzregeln(list(domains), aufloeser=aufloeser, **kw)


@pytest.mark.asyncio
async def test_regeln_pruefen_domain_port_und_aufgeloeste_adresse():
    r = _regeln({"muster.de": ["93.184.216.34"], "cdn.muster.de": ["93.184.216.35"], "intern.muster.de": ["10.0.0.7"],
                 "rebind.muster.de": ["93.184.216.34", "127.0.0.1"], "meta.muster.de": ["169.254.169.254"], "boese.example": ["93.184.216.99"]})
    assert await r.ziel("muster.de", 443, "https") == ("93.184.216.34", 443)
    assert await r.ziel("CDN.Muster.de.", 80, "http") == ("93.184.216.35", 80)
    for host, port, schema, grund in [
        ("boese.example", 443, "https", "Domain-Liste"), ("mustermann.de", 443, "https", "Domain-Liste"),
        ("muster.de.boese.example", 443, "https", "Domain-Liste"), ("muster.de", 8443, "https", "Port"),
        ("muster.de", 22, "http", "Port"), ("127.0.0.1", 443, "https", "IP-Adressen"), ("2130706433", 80, "http", "IP-Adressen"),
        ("[::1]", 443, "https", "IP-Adressen"), ("intern.muster.de", 443, "https", "nicht öffentliche"),
        ("rebind.muster.de", 443, "https", "nicht öffentliche"), ("meta.muster.de", 80, "http", "nicht öffentliche"),
        ("fehlt.muster.de", 443, "https", "nicht auflösbar"),
    ]:
        with pytest.raises(n.Gesperrt, match=grund):
            await r.ziel(host, port, schema)


# --- Lokale Gegenstelle ----------------------------------------------------------------------------------------------

class Gegenstelle:
    """Kleiner HTTP-Server, der jede Anfrage aufzeichnet. Stellt eine Seite, ein Formularziel und einen Download bereit."""

    def __init__(self, tls: ssl.SSLContext | None = None):
        self.anfragen, self.tls = [], tls

    async def __aenter__(self):
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0, ssl=self.tls)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        self.server.close()

    async def _handle(self, r, w):
        try:
            kopf = (await r.readuntil(b"\r\n\r\n")).decode("latin-1")
            zeile = kopf.split("\r\n")[0]
            felder = {k.strip().lower(): v.strip() for k, _, v in (z.partition(":") for z in kopf.split("\r\n")[1:] if z)}
            rumpf = await r.readexactly(int(felder.get("content-length", 0)))
            self.anfragen.append({"zeile": zeile, "kopf": felder, "rumpf": rumpf})
            pfad = zeile.split(" ")[1]
            extra = "Content-Disposition: attachment; filename=\"preise.pdf\"\r\n" if pfad == "/download" else ""
            text = b"<h1>Testseite</h1>" if zeile.startswith("GET") else b"gespeichert"
            w.write(f"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n{extra}Content-Length: {len(text)}\r\n\r\n".encode() + text)
            await w.drain()
        finally:
            w.close()


async def _roh(port, daten: bytes) -> bytes:
    r, w = await asyncio.open_connection("127.0.0.1", port)
    out = b""
    try:  # bei zu großen Anfragen antwortet der Proxy und schließt, bevor alles geschrieben ist
        w.write(daten)
        await w.drain()
        out = await asyncio.wait_for(r.read(), 10)
    except ConnectionError:
        try:
            out = out or await asyncio.wait_for(r.read(), 2)
        except ConnectionError:
            pass
    w.close()
    return out


def _post(host, pfad="/kontakt", rumpf=b"name=Erika+Beispiel&nachricht=Bitte+um+R%C3%BCckruf", extra=""):
    return (f"POST http://{host}{pfad} HTTP/1.1\r\nHost: {host}\r\nContent-Type: application/x-www-form-urlencoded\r\n"
            f"Cookie: sitzung=abc123\r\n{extra}Content-Length: {len(rumpf)}\r\n\r\n").encode() + rumpf


@pytest.mark.asyncio
async def test_lesen_geht_durch_und_alles_mit_aussenwirkung_wird_nie_gesendet():
    async with Gegenstelle() as g:
        regeln = n.Netzregeln(["shop.example"], nur_test_ziele={"shop.example:80": ("127.0.0.1", g.port)})
        proxy = n.Filterproxy(regeln, "lesen")
        port = await proxy.start()
        try:
            antwort = await _roh(port, b"GET http://shop.example/seite?x=1 HTTP/1.1\r\nHost: shop.example\r\nProxy-Connection: keep-alive\r\n\r\n")
            assert antwort.startswith(b"HTTP/1.1 200") and b"Testseite" in antwort
            assert g.anfragen[0]["zeile"] == "GET /seite?x=1 HTTP/1.1" and "proxy-connection" not in g.anfragen[0]["kopf"]
            for methode in ("POST", "PUT", "PATCH", "DELETE"):
                antwort = await _roh(port, _post("shop.example").replace(b"POST", methode.encode(), 1))
                assert antwort.startswith(b"HTTP/1.1 403") and b"X-Jarvis-Gesperrt" in antwort
            antwort = await _roh(port, b"GET http://shop.example/x HTTP/1.1\r\nHost: shop.example\r\nContent-Length: 4\r\n\r\nabcd")
            assert antwort.startswith(b"HTTP/1.1 403")  # lesende Anfrage mit Rumpf
            for kopf in (b"X-HTTP-Method-Override: DELETE", b"x-method-override: POST", b"X-HTTP-Method: PUT"):
                antwort = await _roh(port, b"GET http://shop.example/konto HTTP/1.1\r\nHost: shop.example\r\n" + kopf + b"\r\n\r\n")
                assert antwort.startswith(b"HTTP/1.1 403")  # GET, das sich als schreibend ausgibt
            assert len(g.anfragen) == 1 and proxy.zurueckgehalten == [] and proxy.durchgelassen == 1
            assert [p["was"] for p in proxy.protokoll] == ["POST", "PUT", "PATCH", "DELETE", "GET", "GET", "GET", "GET"]
        finally:
            await proxy.stop()


@pytest.mark.asyncio
async def test_gesperrte_ziele_websocket_und_downloads():
    async with Gegenstelle() as g:
        async def aufloeser(host, port):
            return {"intern.shop.example": ["127.0.0.1"], "meta.shop.example": ["169.254.169.254"]}.get(host, [])
        regeln = n.Netzregeln(["shop.example"], aufloeser=aufloeser, nur_test_ziele={"shop.example:80": ("127.0.0.1", g.port)})
        proxy = n.Filterproxy(regeln, "interaktion")
        port = await proxy.start()
        try:
            for ziel in (f"127.0.0.1:{g.port}", "localhost:8080", "boese.example:443", "169.254.169.254:80", "shop.example:8443",
                         "intern.shop.example:443", "meta.shop.example:443", "[::1]:443"):
                antwort = await _roh(port, f"CONNECT {ziel} HTTP/1.1\r\nHost: {ziel}\r\n\r\n".encode())
                assert antwort.startswith(b"HTTP/1.1 403"), ziel
            for url in (f"http://127.0.0.1:{g.port}/", "http://localhost:8766/internal/leads", "http://boese.example/", "http://intern.shop.example/",
                        "ftp://shop.example/x"):
                antwort = await _roh(port, f"GET {url} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
                assert antwort.split(b" ")[1] in (b"403", b"400"), url
            antwort = await _roh(port, b"GET http://shop.example/ws HTTP/1.1\r\nHost: shop.example\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n\r\n")
            assert antwort.startswith(b"HTTP/1.1 403") and b"WebSocket" in antwort
            assert g.anfragen == []  # nichts davon hat die Gegenstelle erreicht
            antwort = await _roh(port, b"GET http://shop.example/download HTTP/1.1\r\nHost: shop.example\r\n\r\n")
            assert antwort.startswith(b"HTTP/1.1 403") and b"Downloads" in antwort and b"gespeichert" not in antwort
        finally:
            await proxy.stop()


@pytest.mark.asyncio
async def test_interaktion_haelt_die_anfrage_fest_und_sendet_sie_erst_nach_freigabe_unveraendert():
    async with Gegenstelle() as g:
        regeln = n.Netzregeln(["shop.example"], nur_test_ziele={"shop.example:80": ("127.0.0.1", g.port)})
        proxy = n.Filterproxy(regeln, "interaktion")
        port = await proxy.start()
        try:
            antwort = await _roh(port, _post("shop.example"))
            assert antwort.startswith(b"HTTP/1.1 403") and "zurückgehalten".encode() in antwort
            assert g.anfragen == [] and len(proxy.zurueckgehalten) == 1
            gehalten = proxy.zurueckgehalten[0]
            zeilen = {z["feld"]: z["wert"] for z in gehalten.zeilen()}
            assert zeilen["Aktion"] == "Formular absenden (POST)" and zeilen["Website"] == "shop.example"
            assert zeilen["Adresse"] == "http://shop.example/kontakt"
            assert zeilen["Zu sendende Daten"] == "name = Erika Beispiel\nnachricht = Bitte um Rückruf"
            ergebnis = await proxy.senden(gehalten)
            assert ergebnis["status"] == 200 and ergebnis["text"] == "gespeichert"
            assert len(g.anfragen) == 1 and g.anfragen[0]["zeile"] == "POST /kontakt HTTP/1.1"
            assert g.anfragen[0]["rumpf"] == b"name=Erika+Beispiel&nachricht=Bitte+um+R%C3%BCckruf"  # Byte für Byte
            assert g.anfragen[0]["kopf"]["cookie"] == "sitzung=abc123"
            # Obergrenzen: zu große Anfragen und zu viele werden abgelehnt statt festgehalten
            await _roh(port, _post("shop.example", rumpf=b"x" * (n.MAX_KOERPER + 1)))
            assert len(proxy.zurueckgehalten) == 1 and len(g.anfragen) == 1 and "zu groß" in proxy.protokoll[-1]["grund"]
            for _ in range(n.MAX_ZURUECKGEHALTEN + 3):
                await _roh(port, _post("shop.example"))
            assert len(proxy.zurueckgehalten) == n.MAX_ZURUECKGEHALTEN and len(g.anfragen) == 1
        finally:
            await proxy.stop()


def test_anzeige_der_zu_sendenden_daten():
    def gehalten(typ, rumpf, methode="POST"):
        return n.Zurueckgehalten(n.Anfrage(methode, "https", "shop.example", 443, "/api", [("Content-Type", typ)], rumpf))
    assert json.loads(gehalten("application/json", b'{"menge": 2, "artikel": "A1"}').daten()) == {"menge": 2, "artikel": "A1"}
    multi = (b"--XX\r\nContent-Disposition: form-data; name=\"betreff\"\r\n\r\nAnfrage\r\n--XX\r\nContent-Disposition: form-data; "
             b"name=\"anhang\"; filename=\"lebenslauf.pdf\"\r\nContent-Type: application/pdf\r\n\r\n%PDF-1.4\r\n--XX--\r\n")
    g = gehalten("multipart/form-data; boundary=XX", multi)
    assert g.art() == "Formular mit Datei-Upload absenden" and "betreff = Anfrage" in g.daten() and "lebenslauf.pdf" in g.daten()
    assert gehalten("application/octet-stream", b"\xff\xfe\x00").daten().startswith("3 Bytes")
    assert gehalten("", b"", "DELETE").art() == "Löschen" and "keine Daten" in gehalten("", b"", "DELETE").daten()
    assert "weitere Zeichen" in gehalten("text/plain", b"a" * 5000).daten()


def test_freigegebenes_senden_prueft_die_netzregeln_erneut():
    """Auch nach der Freigabe geht nichts an eine nicht öffentliche Adresse oder eine fremde Domain."""
    async def aufloeser(host, port):
        return ["127.0.0.1"]
    anfrage = n.Anfrage("POST", "http", "shop.example", 80, "/kontakt", [("Content-Type", "text/plain")], b"x")
    with pytest.raises(n.Gesperrt, match="nicht öffentliche"):
        n.sende_freigegeben(n.Zurueckgehalten(anfrage), ["shop.example"], regeln=n.Netzregeln(["shop.example"], aufloeser=aufloeser))
    with pytest.raises(n.Gesperrt, match="Domain-Liste"):
        n.sende_freigegeben(n.Zurueckgehalten(anfrage), ["andere.example"], regeln=n.Netzregeln(["andere.example"], aufloeser=aufloeser))


# --- HTTPS -----------------------------------------------------------------------------------------------------------

def _test_tls(tmp_path, host):
    """Selbst signiertes Zertifikat für die lokale HTTPS-Gegenstelle und ein Kontext, der genau ihm vertraut."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(1)
            .not_valid_before(now - datetime.timedelta(minutes=1)).not_valid_after(now + datetime.timedelta(hours=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False).sign(key, hashes.SHA256()))
    (tmp_path / "c.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (tmp_path / "k.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(str(tmp_path / "c.pem"), str(tmp_path / "k.pem"))
    client = ssl.create_default_context(cafile=str(tmp_path / "c.pem"))
    return server, client


@pytest.mark.asyncio
async def test_https_wird_eingesehen_get_geht_durch_post_wird_festgehalten(tmp_path):
    server_tls, vertraut = _test_tls(tmp_path, "shop.example")
    async with Gegenstelle(server_tls) as g:
        regeln = n.Netzregeln(["shop.example"], nur_test_ziele={"shop.example:443": ("127.0.0.1", g.port)})
        proxy = n.Filterproxy(regeln, "interaktion", upstream_ssl=vertraut)
        port = await proxy.start()

        async def ueber_tunnel(daten: bytes):
            r, w = await asyncio.open_connection("127.0.0.1", port)
            w.write(b"CONNECT shop.example:443 HTTP/1.1\r\nHost: shop.example:443\r\n\r\n")
            assert (await r.readuntil(b"\r\n\r\n")).startswith(b"HTTP/1.1 200")
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE  # geprüft wird unten der Schlüssel (wie Chromes SPKI-Liste)
            await w.start_tls(ctx, server_hostname="shop.example")
            from cryptography import x509
            from cryptography.hazmat.primitives import serialization
            cert = x509.load_der_x509_certificate(w.get_extra_info("ssl_object").getpeercert(binary_form=True))
            spki = cert.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
            assert base64.b64encode(hashlib.sha256(spki).digest()).decode() == proxy.zertifikate.spki().split(",")[0]
            assert w.get_extra_info("ssl_object").selected_alpn_protocol() in (None, "http/1.1")
            w.write(daten)
            await w.drain()
            out = await asyncio.wait_for(r.read(), 10)
            w.close()
            return out

        try:
            antwort = await ueber_tunnel(b"GET /seite HTTP/1.1\r\nHost: shop.example\r\n\r\n")
            assert antwort.startswith(b"HTTP/1.1 200") and b"Testseite" in antwort and len(g.anfragen) == 1
            rumpf = b'{"email": "erika@example.org"}'
            antwort = await ueber_tunnel(b"POST /newsletter HTTP/1.1\r\nHost: shop.example\r\nContent-Type: application/json\r\n"
                                         b"Content-Length: " + str(len(rumpf)).encode() + b"\r\n\r\n" + rumpf)
            assert antwort.startswith(b"HTTP/1.1 403") and len(g.anfragen) == 1 and len(proxy.zurueckgehalten) == 1
            assert proxy.zurueckgehalten[0].anfrage.url == "https://shop.example/newsletter"
            assert (await proxy.senden(proxy.zurueckgehalten[0]))["status"] == 200 and g.anfragen[1]["rumpf"] == rumpf
        finally:
            await proxy.stop()
    assert not proxy.zertifikate.ordner.exists()  # Schlüssel des Laufs sind weg


@pytest.mark.asyncio
async def test_https_gegenstelle_mit_falschem_zertifikat_wird_nicht_durchgereicht(tmp_path):
    server_tls, _ = _test_tls(tmp_path, "shop.example")
    async with Gegenstelle(server_tls) as g:
        regeln = n.Netzregeln(["shop.example"], nur_test_ziele={"shop.example:443": ("127.0.0.1", g.port)})
        proxy = n.Filterproxy(regeln, "lesen")  # normaler Kontext: vertraut dem Testzertifikat NICHT
        port = await proxy.start()
        try:
            r, w = await asyncio.open_connection("127.0.0.1", port)
            w.write(b"CONNECT shop.example:443 HTTP/1.1\r\n\r\n")
            await r.readuntil(b"\r\n\r\n")
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
            await w.start_tls(ctx, server_hostname="shop.example")
            w.write(b"GET / HTTP/1.1\r\nHost: shop.example\r\n\r\n")
            assert (await asyncio.wait_for(r.read(), 10)).startswith(b"HTTP/1.1 502") and g.anfragen == []
            w.close()
        finally:
            await proxy.stop()


# --- LLM-Relay -------------------------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_relay_haengt_den_schluessel_erst_draussen_an_und_laesst_nur_messages_durch():
    import aiohttp
    from aiohttp import web

    gesehen = []

    async def messages(request):
        gesehen.append({"key": request.headers.get("x-api-key"), "ws": request.headers.get("anthropic-workspace-id"), "body": await request.json()})
        return web.json_response({"id": "msg_1", "content": [{"type": "text", "text": "ok"}]})
    app = web.Application()
    app.router.add_post("/v1/messages", messages)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    upstream = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    relay = n.LlmRelay("sk-echter-schluessel", "claude-test", workspace="ws-1", max_anfragen=2, upstream=upstream)
    port = await relay.start()
    basis = f"http://127.0.0.1:{port}"
    try:
        async with aiohttp.ClientSession() as s:
            body = {"model": "claude-test", "messages": []}
            for kopf in ({}, {"x-api-key": "falsch"}, {"x-api-key": "sk-echter-schluessel"}):
                async with s.post(basis + "/v1/messages", json=body, headers=kopf) as r:
                    assert r.status == 401
            ok = {"x-api-key": relay.kennwort, "anthropic-version": "2023-06-01"}
            async with s.get(basis + "/v1/models", headers=ok) as r:
                assert r.status == 404
            async with s.post(basis + "/v1/files", json=body, headers=ok) as r:
                assert r.status == 404
            assert gesehen == []
            async with s.post(basis + "/v1/messages", json=body, headers=ok) as r:
                text = await r.text()
                assert r.status == 200 and json.loads(text)["id"] == "msg_1" and "sk-echter" not in text
            assert gesehen == [{"key": "sk-echter-schluessel", "ws": "ws-1", "body": body}]
            async with s.post(basis + "/v1/messages", json={"model": "anderes-modell", "messages": []}, headers=ok) as r:
                assert r.status == 400
            async with s.post(basis + "/v1/messages", json=body, headers=ok) as r:
                assert r.status == 429  # Anfragegrenze des Laufs
            assert len(gesehen) == 1 and relay.kennwort != "sk-echter-schluessel"
    finally:
        await relay.stop()
        await runner.cleanup()


def test_netz_startet_und_raeumt_auf():
    regeln = n.Netzregeln(["muster.de"])
    with n.Netz(regeln, "lesen", n.LlmRelay("sk-x", "claude-test")) as netz:
        assert netz.proxy.port > 0 and netz.relay.port > 0 and netz.proxy.zertifikate.ordner.exists()
        ordner = netz.proxy.zertifikate.ordner
    assert not ordner.exists() and not netz._thread.is_alive()
