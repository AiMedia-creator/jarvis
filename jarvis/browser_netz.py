"""Netzgrenzen für den Browser-Agenten – durchgesetzt im Jarvis-Prozess, AUSSERHALB von Browser Use und seinem Prompt.

Browser Use 1.0 führt vom Modell geschriebenen Python-Code aus und kennt weder Freigabe-Hooks noch eine Netzsperre
(``allowed_domains`` ist laut README „a guard for an aligned model, not a sandbox“). Deshalb liegen die Grenzen hier:

1. ``Filterproxy`` – der einzige Weg des Wegwerf-Chrome ins Netz (die Sandbox erlaubt ihm nur diesen Port).
   - Nur Hosts der Domain-Liste (und ihre Unterdomains), nur Port 80/443, nie IP-Adressen als Ziel.
   - Der Proxy löst selbst auf und verbindet zur geprüften Adresse: Loopback, private Netze, Link-Local und
     Metadaten-Adressen (169.254.169.254), CGNAT, NAT64/6to4/Teredo sind gesperrt – auch bei DNS-Rebinding.
   - HTTPS wird mit einem Zertifikat aufgebrochen, das nur für diesen Lauf existiert (der Wegwerf-Chrome vertraut
     genau diesem Schlüssel). Nur so sieht der Proxy die HTTP-Methode.
   - GET/HEAD/OPTIONS gehen durch. Alles andere (POST/PUT/PATCH/DELETE = Formular absenden, senden, löschen, ändern,
     kaufen, hochladen) wird NICHT gesendet. Im Interaktionsmodus wird die Anfrage festgehalten und kann nach einer
     gesonderten Freigabe genau so (Byte für Byte) gesendet werden. WebSockets und Downloads sind gesperrt.
2. ``LLM-Relay`` – der Agenten-Prozess bekommt nie den Anthropic-Schlüssel, nur ein Einmal-Kennwort für diesen Lauf.
   Das Relay hängt den echten Schlüssel an und reicht ausschließlich ``POST /v1/messages`` für das festgelegte Modell
   durch, mit Obergrenze für die Zahl der Anfragen.

Bekannte Grenzen stehen in ``docs/BROWSER-AGENT.md`` (u. a.: GET-Anfragen mit Nebenwirkung, Unterdomains).
"""

from __future__ import annotations

import asyncio
import base64
import datetime
import hashlib
import hmac
import ipaddress
import json
import secrets
import shutil
import socket
import ssl
import tempfile
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

SICHER = frozenset({"GET", "HEAD", "OPTIONS"})
MAX_KOPF = 64 * 1024
MAX_KOERPER = 1_000_000          # größere Anfragen werden nicht festgehalten, nur abgelehnt
MAX_ZURUECKGEHALTEN = 20
_HOP = {"connection", "proxy-connection", "proxy-authorization", "keep-alive", "te", "trailer", "upgrade", "expect",
        "transfer-encoding"}
# Manche Server behandeln ein GET mit diesen Kopfzeilen wie POST/DELETE. Solche Anfragen gelten nie als lesend.
_METHODE_UEBERSCHREIBEN = {"x-http-method-override", "x-http-method", "x-method-override"}
_NAT = [ipaddress.ip_network(n) for n in ("64:ff9b::/96", "64:ff9b:1::/48", "2002::/16", "2001::/32")]  # betten IPv4 ein


class Gesperrt(Exception):
    """Ziel oder Anfrage ist nach den Netzregeln nicht erlaubt (Text für Protokoll und Bericht)."""


def ip_oeffentlich(ip: str) -> bool:
    """Nur weltweit routbare Adressen. Loopback, privat, Link-Local/Metadaten, CGNAT, reserviert, Multicast: nein."""
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    if isinstance(a, ipaddress.IPv6Address):
        if a.ipv4_mapped is not None:
            a = a.ipv4_mapped
        elif any(a in n for n in _NAT):
            return False
    return bool(a.is_global) and not a.is_multicast


def _ist_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return host.replace(".", "").isdigit()  # auch „127.1“ oder „2130706433“


async def _dns(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(i[4][0] for i in infos))


class Netzregeln:
    """Welche Ziele der Browser erreichen darf. ``nur_test_ziele`` bildet in Tests „host:port“ auf eine lokale
    Testseite ab; im Betrieb bleibt es leer, dann ist jede nicht öffentliche Adresse gesperrt."""

    def __init__(self, domains: list[str], *, aufloeser=None, nur_test_ziele: dict[str, tuple[str, int]] | None = None):
        self.domains = [d.lower().strip(".") for d in domains if d.strip(".")]
        self._aufloeser = aufloeser or _dns
        self.nur_test_ziele = dict(nur_test_ziele or {})

    def host_erlaubt(self, host: str) -> bool:
        h = host.lower().rstrip(".")
        return any(h == d or h.endswith("." + d) for d in self.domains)

    async def ziel(self, host: str, port: int, schema: str) -> tuple[str, int]:
        """Geprüfte Verbindungsadresse (IP, Port) – verbunden wird genau dorthin, es wird nicht erneut aufgelöst."""
        host = host.lower().rstrip(".")
        if f"{host}:{port}" in self.nur_test_ziele:
            return self.nur_test_ziele[f"{host}:{port}"]
        if _ist_ip(host):
            raise Gesperrt("IP-Adressen als Ziel sind gesperrt")
        if not self.host_erlaubt(host):
            raise Gesperrt(f"{host} steht nicht auf der Domain-Liste")
        if port != (443 if schema == "https" else 80):
            raise Gesperrt(f"Port {port} ist nicht erlaubt")
        try:
            ips = await asyncio.wait_for(self._aufloeser(host, port), 10)
        except (OSError, asyncio.TimeoutError) as exc:
            raise Gesperrt(f"{host} ist nicht auflösbar") from exc
        if not ips:
            raise Gesperrt(f"{host} ist nicht auflösbar")
        if any(not ip_oeffentlich(i) for i in ips):
            raise Gesperrt(f"{host} zeigt auf eine nicht öffentliche Adresse (Loopback, privates Netz oder Metadaten)")
        return ips[0], port


# --- Anfragen -------------------------------------------------------------------------------------------------------

@dataclass
class Anfrage:
    methode: str
    schema: str
    host: str
    port: int
    pfad: str
    kopf: list[tuple[str, str]]
    koerper: bytes = b""

    @property
    def url(self) -> str:
        standard = 443 if self.schema == "https" else 80
        return f"{self.schema}://{self.host}{'' if self.port == standard else f':{self.port}'}{self.pfad}"

    def wert(self, name: str) -> str:
        return next((v for k, v in self.kopf if k.lower() == name), "")

    def roh(self) -> bytes:
        """Die Anfrage, wie sie an den Server geht: ohne Proxy-Kopfzeilen, eine Anfrage je Verbindung."""
        zeilen = [f"{self.methode} {self.pfad} HTTP/1.1"]
        zeilen += [f"{k}: {v}" for k, v in self.kopf if k.lower() not in _HOP and k.lower() != "content-length"]
        if self.koerper or self.methode not in SICHER:
            zeilen.append(f"Content-Length: {len(self.koerper)}")
        zeilen.append("Connection: close")
        return ("\r\n".join(zeilen) + "\r\n\r\n").encode("latin-1") + self.koerper


@dataclass
class Zurueckgehalten:
    """Eine Anfrage mit Außenwirkung, die NICHT gesendet wurde. Liegt nur im Arbeitsspeicher, verfällt, gilt einmal."""
    anfrage: Anfrage
    id: str = field(default_factory=lambda: "BA-" + secrets.token_hex(5))
    zeit: float = field(default_factory=time.time)

    def art(self) -> str:
        typ = self.anfrage.wert("content-type").lower()
        if "multipart/form-data" in typ:
            return "Formular mit Datei-Upload absenden" if b"filename=" in self.anfrage.koerper else "Formular absenden"
        if "application/x-www-form-urlencoded" in typ:
            return "Formular absenden"
        return {"DELETE": "Löschen", "PUT": "Ändern", "PATCH": "Ändern"}.get(self.anfrage.methode, "Daten senden")

    def daten(self, grenze: int = 1500) -> str:
        """Die zu sendenden Daten in lesbarer Form (vollständig bis zur Grenze, nie stillschweigend verändert)."""
        a, typ = self.anfrage, self.anfrage.wert("content-type").lower()
        if not a.koerper:
            return "(keine Daten im Rumpf)"
        try:
            if "application/x-www-form-urlencoded" in typ:
                paare = urllib.parse.parse_qsl(a.koerper.decode("utf-8"), keep_blank_values=True)
                text = "\n".join(f"{k} = {v}" for k, v in paare)
            elif "application/json" in typ:
                text = json.dumps(json.loads(a.koerper.decode("utf-8")), ensure_ascii=False, indent=1)
            elif "multipart/form-data" in typ:
                text = _multipart_text(a.koerper, a.wert("content-type"))  # Trennzeichen in Originalschreibweise
            else:
                text = a.koerper.decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            return f"{len(a.koerper)} Bytes ({typ or 'unbekannter Typ'}), nicht als Text darstellbar"
        return text if len(text) <= grenze else text[:grenze] + f"\n… ({len(text) - grenze} weitere Zeichen)"

    def zeilen(self) -> list[dict]:
        a = self.anfrage
        return [{"feld": "Aktion", "wert": f"{self.art()} ({a.methode})"}, {"feld": "Website", "wert": a.host},
                {"feld": "Adresse", "wert": a.url[:300]}, {"feld": "Zu sendende Daten", "wert": self.daten()}]


def _multipart_text(koerper: bytes, typ: str) -> str:
    grenze = next((p.split("=", 1)[1].strip('"') for p in typ.split(";") if p.strip().startswith("boundary=")), "")
    teile = []
    for teil in koerper.split(b"--" + grenze.encode())[1:-1] if grenze else []:
        kopf, _, wert = teil.strip(b"\r\n").partition(b"\r\n\r\n")
        disp = kopf.decode("utf-8", "replace")
        name = disp.split('name="', 1)[1].split('"', 1)[0] if 'name="' in disp else "?"
        if 'filename="' in disp:
            datei = disp.split('filename="', 1)[1].split('"', 1)[0]
            teile.append(f"{name} = [Datei „{datei}“, {len(wert)} Bytes]")
        else:
            teile.append(f"{name} = {wert.decode('utf-8', 'replace')}")
    return "\n".join(teile) or f"{len(koerper)} Bytes Formulardaten"


async def _lies_kopf(r: asyncio.StreamReader) -> tuple[str, list[tuple[str, str]]]:
    roh = await asyncio.wait_for(r.readuntil(b"\r\n\r\n"), 30)
    zeilen = roh.decode("latin-1").split("\r\n")
    kopf = []
    for z in zeilen[1:]:
        if z:
            k, _, v = z.partition(":")
            kopf.append((k.strip(), v.strip()))
    return zeilen[0], kopf


async def _lies_koerper(r: asyncio.StreamReader, kopf: list[tuple[str, str]], grenze: int) -> bytes | None:
    """Rumpf der Anfrage; ``None``, wenn er die Grenze übersteigt (dann wird nicht weitergelesen)."""
    werte = {k.lower(): v for k, v in kopf}
    if "chunked" in werte.get("transfer-encoding", "").lower():
        teile, summe = [], 0
        while True:
            laenge = int((await asyncio.wait_for(r.readline(), 30)).split(b";")[0].strip() or b"0", 16)
            if laenge == 0:
                await r.readline()
                return b"".join(teile)
            summe += laenge
            if summe > grenze:
                return None
            teile.append(await asyncio.wait_for(r.readexactly(laenge), 30))
            await r.readline()
    try:
        laenge = int(werte.get("content-length", "0") or 0)
    except ValueError as exc:
        raise Gesperrt("ungültige Content-Length") from exc
    if laenge < 0 or laenge > grenze:
        return None
    return await asyncio.wait_for(r.readexactly(laenge), 30) if laenge else b""


def _antwort(status: int, grund: str, text: str) -> bytes:
    rumpf = (f"<!doctype html><meta charset=utf-8><title>Jarvis</title><h1>{grund}</h1><p>{text}</p>").encode("utf-8")
    return (f"HTTP/1.1 {status} {grund.encode('ascii', 'ignore').decode() or 'Blocked'}\r\nContent-Type: text/html; charset=utf-8\r\n"
            f"Content-Length: {len(rumpf)}\r\nX-Jarvis-Gesperrt: 1\r\nConnection: close\r\n\r\n").encode("latin-1") + rumpf


# --- Zertifikate für diesen einen Lauf -------------------------------------------------------------------------------

class Laufzertifikate:
    """Eine Zertifizierungsstelle und ein Schlüssel, die nur für einen Lauf leben. Der Wegwerf-Chrome vertraut genau
    diesem Schlüssel (``--ignore-certificate-errors-spki-list``); kein anderer Browser, kein Schlüsselbund."""

    def __init__(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID

        self._x509, self._hashes, self._ser, self._oid = x509, hashes, serialization, NameOID
        self.ordner = Path(tempfile.mkdtemp(prefix="jarvis-laufca-"))
        self._ca_key = ec.generate_private_key(ec.SECP256R1())
        self._key = ec.generate_private_key(ec.SECP256R1())
        now = datetime.datetime.now(datetime.timezone.utc)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Jarvis Browser-Lauf (nur lokal, nur dieser Lauf)")])
        self._ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(self._ca_key.public_key())
                    .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
                    .not_valid_after(now + datetime.timedelta(hours=2))
                    .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                    .sign(self._ca_key, hashes.SHA256()))
        (self.ordner / "key.pem").write_bytes(self._key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                                      serialization.NoEncryption()))
        (self.ordner / "key.pem").chmod(0o600)
        self._kontexte: dict[str, ssl.SSLContext] = {}

    def spki(self) -> str:
        """Base64-SHA-256 der öffentlichen Schlüssel (Blatt und Stelle) für Chromes SPKI-Liste."""
        def h(key) -> str:
            der = key.public_key().public_bytes(self._ser.Encoding.DER, self._ser.PublicFormat.SubjectPublicKeyInfo)
            return base64.b64encode(hashlib.sha256(der).digest()).decode()
        return f"{h(self._key)},{h(self._ca_key)}"

    def kontext(self, host: str) -> ssl.SSLContext:
        if host not in self._kontexte:
            x509, now = self._x509, datetime.datetime.now(datetime.timezone.utc)
            cert = (x509.CertificateBuilder()
                    .subject_name(x509.Name([x509.NameAttribute(self._oid.COMMON_NAME, host[:64])]))
                    .issuer_name(self._ca.subject).public_key(self._key.public_key()).serial_number(x509.random_serial_number())
                    .not_valid_before(now - datetime.timedelta(minutes=5)).not_valid_after(now + datetime.timedelta(hours=2))
                    .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
                    .sign(self._ca_key, self._hashes.SHA256()))
            pfad = self.ordner / f"{hashlib.sha256(host.encode()).hexdigest()[:24]}.pem"
            pfad.write_bytes(cert.public_bytes(self._ser.Encoding.PEM) + self._ca.public_bytes(self._ser.Encoding.PEM))
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(str(pfad), str(self.ordner / "key.pem"))
            ctx.set_alpn_protocols(["http/1.1"])  # kein HTTP/2: der Proxy liest jede Anfrage einzeln
            self._kontexte[host] = ctx
        return self._kontexte[host]

    def weg(self) -> None:
        shutil.rmtree(self.ordner, ignore_errors=True)


# --- Filterproxy -----------------------------------------------------------------------------------------------------

class Filterproxy:
    """HTTP-Proxy für den Wegwerf-Chrome. ``modus``: ``lesen`` (nichts mit Außenwirkung, nichts festhalten) oder
    ``interaktion`` (Anfragen mit Außenwirkung festhalten statt senden)."""

    def __init__(self, regeln: Netzregeln, modus: str = "lesen", *, upstream_ssl: ssl.SSLContext | None = None):
        if modus not in ("lesen", "interaktion"):
            raise ValueError("modus muss lesen oder interaktion sein")
        self.regeln, self.modus = regeln, modus
        self.zertifikate = Laufzertifikate()
        self._upstream_ssl = upstream_ssl or ssl.create_default_context()
        self.zurueckgehalten: list[Zurueckgehalten] = []
        self.protokoll: list[dict] = []   # was gesperrt wurde (für den Bericht), höchstens 200 Einträge
        self.durchgelassen = 0
        self.server: asyncio.AbstractServer | None = None
        self.port = 0

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._verbindung, "127.0.0.1", 0, limit=MAX_KOPF)
        self.port = self.server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self) -> None:
        if self.server:
            self.server.close()
        self.zertifikate.weg()

    def _merke(self, was: str, ziel: str, grund: str) -> None:
        if len(self.protokoll) < 200:
            self.protokoll.append({"was": was, "ziel": ziel[:200], "grund": grund})

    async def _verbindung(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        try:
            zeile, kopf = await _lies_kopf(r)
            methode, _, rest = zeile.partition(" ")
            ziel = rest.rsplit(" ", 1)[0]
            if methode.upper() == "CONNECT":
                host, _, port = ziel.rpartition(":")
                host = host.strip("[]").lower()
                try:
                    await self.regeln.ziel(host, int(port or 0), "https")
                except (Gesperrt, ValueError) as exc:
                    self._merke("Verbindung", ziel, str(exc))
                    w.write(_antwort(403, "Von Jarvis gesperrt", str(exc)))
                    return
                w.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await w.drain()
                await asyncio.wait_for(w.start_tls(self.zertifikate.kontext(host)), 20)
                zeile, kopf = await _lies_kopf(r)
                methode, _, rest = zeile.partition(" ")
                await self._anfrage(r, w, methode.upper(), "https", host, int(port), rest.rsplit(" ", 1)[0], kopf)
            else:
                teile = urllib.parse.urlsplit(ziel)
                if teile.scheme != "http" or not teile.hostname:
                    w.write(_antwort(400, "Ungültige Anfrage", "Nur http und https."))
                    return
                pfad = (teile.path or "/") + (f"?{teile.query}" if teile.query else "")
                await self._anfrage(r, w, methode.upper(), "http", teile.hostname.lower(), teile.port or 80, pfad, kopf)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError, ConnectionError, ssl.SSLError, OSError, ValueError):
            pass  # abgebrochene oder kaputte Verbindung: nichts weiterleiten
        finally:
            try:
                await w.drain()
            except Exception:
                pass
            w.close()

    async def _anfrage(self, r, w, methode: str, schema: str, host: str, port: int, pfad: str, kopf: list[tuple[str, str]]) -> None:
        anfrage = Anfrage(methode, schema, host, port, pfad if pfad.startswith("/") else "/" + pfad, kopf)
        werte = {k.lower(): v.lower() for k, v in kopf}
        if "upgrade" in werte.get("connection", "") or werte.get("upgrade"):
            self._merke("WebSocket", anfrage.url, "dauerhafte Verbindungen sind gesperrt")
            w.write(_antwort(403, "Von Jarvis gesperrt", "WebSocket-Verbindungen sind in diesem Browser gesperrt."))
            return
        if methode not in SICHER:
            koerper = await _lies_koerper(r, kopf, MAX_KOERPER)
            if self.modus == "interaktion" and koerper is not None and len(self.zurueckgehalten) < MAX_ZURUECKGEHALTEN:
                anfrage.koerper = koerper
                gehalten = Zurueckgehalten(anfrage)
                self.zurueckgehalten.append(gehalten)
                self._merke(methode, anfrage.url, f"festgehalten als {gehalten.id} – wartet auf deine Freigabe")
                w.write(_antwort(403, "Von Jarvis zurückgehalten",
                                 f"Diese Aktion ({methode}) wurde NICHT gesendet. Sie wartet als {gehalten.id} auf die Freigabe des Nutzers."))
            else:
                grund = ("nur lesender Modus" if self.modus == "lesen" else
                         "zu groß zum Festhalten" if koerper is None else "zu viele festgehaltene Aktionen")
                self._merke(methode, anfrage.url, f"nicht gesendet ({grund})")
                w.write(_antwort(403, "Von Jarvis gesperrt", f"Anfragen mit Außenwirkung ({methode}) werden nicht gesendet: {grund}."))
            return
        if _METHODE_UEBERSCHREIBEN & set(werte):
            self._merke(methode, anfrage.url, "Kopfzeile, die die Methode überschreibt")
            w.write(_antwort(403, "Von Jarvis gesperrt", "Anfragen, die ihre Methode per Kopfzeile ändern, werden nicht gesendet."))
            return
        koerper = await _lies_koerper(r, kopf, MAX_KOERPER)
        if koerper:  # GET mit Rumpf ist unüblich und könnte Daten nach außen tragen
            self._merke(methode, anfrage.url, "Lesende Anfrage mit Rumpf")
            w.write(_antwort(403, "Von Jarvis gesperrt", "Lesende Anfragen dürfen keine Daten im Rumpf tragen."))
            return
        try:
            await self._weiterleiten(anfrage, w, downloads_sperren=True)
            self.durchgelassen += 1
        except Gesperrt as exc:
            self._merke(methode, anfrage.url, str(exc))
            w.write(_antwort(403, "Von Jarvis gesperrt", str(exc)))
        except (OSError, asyncio.TimeoutError, ssl.SSLError, asyncio.IncompleteReadError) as exc:
            w.write(_antwort(502, "Nicht erreichbar", f"{host}: {type(exc).__name__}"))

    async def _weiterleiten(self, anfrage: Anfrage, w: asyncio.StreamWriter | None, *, downloads_sperren: bool,
                            sammeln: int = 0) -> tuple[int, list[tuple[str, str]], bytes]:
        """Sendet die Anfrage an die GEPRÜFTE Adresse. Mit ``w`` wird die Antwort durchgereicht, sonst bis ``sammeln``
        Bytes gesammelt (für das Senden einer freigegebenen Aktion)."""
        ip, port = await self.regeln.ziel(anfrage.host, anfrage.port, anfrage.schema)
        https = anfrage.schema == "https"
        ur, uw = await asyncio.wait_for(asyncio.open_connection(
            ip, port, ssl=self._upstream_ssl if https else None, server_hostname=anfrage.host if https else None, limit=MAX_KOPF), 20)
        try:
            uw.write(anfrage.roh())
            await uw.drain()
            roh = await asyncio.wait_for(ur.readuntil(b"\r\n\r\n"), 60)
            zeilen = roh.decode("latin-1").split("\r\n")
            status = int(zeilen[0].split(" ")[1])
            kopf = [(k.strip(), v.strip()) for k, _, v in (z.partition(":") for z in zeilen[1:] if z)]
            werte = {k.lower(): v for k, v in kopf}
            if downloads_sperren and werte.get("content-disposition", "").lower().lstrip().startswith("attachment"):
                raise Gesperrt("Downloads sind gesperrt (Datei wurde nicht geladen)")
            if w is None:
                rumpf = b""
                while len(rumpf) < sammeln:
                    stueck = await asyncio.wait_for(ur.read(65536), 60)
                    if not stueck:
                        break
                    rumpf += stueck
                return status, kopf, rumpf[:sammeln]
            w.write(roh)
            while True:
                stueck = await asyncio.wait_for(ur.read(65536), 120)
                if not stueck:
                    break
                w.write(stueck)
                await w.drain()
            return status, kopf, b""
        finally:
            uw.close()

    async def senden(self, gehalten: Zurueckgehalten) -> dict:
        """Sendet eine festgehaltene Anfrage unverändert – nur nach der gesonderten Freigabe aufzurufen."""
        status, kopf, rumpf = await self._weiterleiten(gehalten.anfrage, None, downloads_sperren=False, sammeln=20_000)
        werte = {k.lower(): v for k, v in kopf}
        text = rumpf.decode("utf-8", "replace") if "text" in werte.get("content-type", "") or "json" in werte.get("content-type", "") else ""
        return {"status": status, "weiterleitung": werte.get("location", ""), "typ": werte.get("content-type", ""), "text": text[:4000]}


# --- LLM-Relay -------------------------------------------------------------------------------------------------------

class LlmRelay:
    """Reicht ``POST /v1/messages`` an Anthropic durch und hängt erst hier den echten Schlüssel an."""

    def __init__(self, schluessel: str, modell: str, *, workspace: str = "", max_anfragen: int = 120,
                 upstream: str = "https://api.anthropic.com"):
        self._schluessel, self.modell, self._workspace = schluessel, modell, workspace
        self.kennwort = secrets.token_urlsafe(32)   # gilt nur für diesen Lauf und nur an diesem Relay
        self.max_anfragen, self.anfragen, self._upstream = max_anfragen, 0, upstream.rstrip("/")
        self._runner = None
        self._session = None
        self.port = 0

    async def start(self) -> int:
        import aiohttp
        from aiohttp import web

        app = web.Application(client_max_size=24 * 1024 * 1024)
        app.router.add_route("*", "/{pfad:.*}", self._handle)
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=300))
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        return self.port

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
        if self._session:
            await self._session.close()

    async def _handle(self, request):
        from aiohttp import web

        if not hmac.compare_digest(request.headers.get("x-api-key", ""), self.kennwort):
            return web.json_response({"error": {"type": "authentication_error", "message": "Relay-Kennwort fehlt."}}, status=401)
        if request.method != "POST" or request.path != "/v1/messages":
            return web.json_response({"error": {"type": "not_found_error", "message": "Nur /v1/messages."}}, status=404)
        self.anfragen += 1
        if self.anfragen > self.max_anfragen:
            return web.json_response({"error": {"type": "rate_limit_error", "message": "Anfragegrenze dieses Laufs erreicht."}}, status=429)
        roh = await request.read()
        try:
            if json.loads(roh).get("model") != self.modell:
                raise ValueError
        except (ValueError, AttributeError):
            return web.json_response({"error": {"type": "invalid_request_error", "message": "Anderes Modell als freigegeben."}}, status=400)
        kopf = {k: request.headers[k] for k in ("anthropic-version", "anthropic-beta", "content-type", "accept") if k in request.headers}
        kopf["x-api-key"] = self._schluessel
        if self._workspace:
            kopf["anthropic-workspace-id"] = self._workspace
        async with self._session.post(self._upstream + "/v1/messages", data=roh, headers=kopf, params=request.query) as antwort:
            out = web.StreamResponse(status=antwort.status, headers={"content-type": antwort.headers.get("content-type", "application/json")})
            await out.prepare(request)
            async for stueck in antwort.content.iter_any():
                await out.write(stueck)
            await out.write_eof()
            return out


# --- Alles zusammen: eigener Thread mit eigener Ereignisschleife ---------------------------------------------------

class Netz:
    """Startet Filterproxy und (optional) LLM-Relay in einem eigenen Thread. ``with Netz(...) as n:`` räumt sicher auf."""

    def __init__(self, regeln: Netzregeln, modus: str, relay: LlmRelay | None = None, *, upstream_ssl: ssl.SSLContext | None = None):
        self.proxy = Filterproxy(regeln, modus, upstream_ssl=upstream_ssl)
        self.relay = relay
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="jarvis-browser-netz", daemon=True)

    def _lauf(self, coro, timeout: float = 30):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    def __enter__(self) -> "Netz":
        self._thread.start()
        try:
            self._lauf(self.proxy.start())
            if self.relay:
                self._lauf(self.relay.start())
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *exc) -> None:
        try:
            if self._thread.is_alive():
                self._lauf(self.proxy.stop(), 10)
                if self.relay:
                    self._lauf(self.relay.stop(), 10)
        except Exception:
            self.proxy.zertifikate.weg()
        finally:
            if self._thread.is_alive():
                self._loop.call_soon_threadsafe(self._loop.stop)
                self._thread.join(5)
            self.proxy.zertifikate.weg()


def sende_freigegeben(gehalten: Zurueckgehalten, domains: list[str], *, regeln: Netzregeln | None = None,
                      upstream_ssl: ssl.SSLContext | None = None) -> dict:
    """Sendet EINE freigegebene, festgehaltene Anfrage – mit denselben Netzregeln wie der Lauf (Domain-Liste, keine
    nicht öffentlichen Adressen)."""
    proxy = Filterproxy(regeln or Netzregeln(domains), "interaktion", upstream_ssl=upstream_ssl)
    try:
        return asyncio.run(asyncio.wait_for(proxy.senden(gehalten), 60))
    finally:
        proxy.zertifikate.weg()
