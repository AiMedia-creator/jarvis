# Browser-Agent über Browser Use (Stand 12.11.2026)

Quelle: github.com/browser-use/browser-use, MIT, Version **1.0.2** (fest angepinnt, `==`), installiert in ein eigenes venv
`~/.jarvis/venvs/browser-use` (nicht im Jarvis-venv). Code: `jarvis/browser_agent.py` (Ablauf, Sandboxen, Freigaben),
`jarvis/browser_netz.py` (Netzfilter, LLM-Relay), `jarvis/browser_agent_runner.py` (läuft im browser-use-venv).

## Was geprüft wurde (12.11.2026)

Offizielles Repository (README, `AGENTS.md`, `MIGRATING.md`) und der installierte Quelltext von 1.0.2:

- 1.0.2 ist die Version, die README und Installationsanleitung nennen; der Hersteller verlangt einen exakten Pin.
- Der Agent ist „the model, a REPL and CDP“: Das Modell schreibt **Python-Code**, der lokal in einem Unterprozess läuft
  (mit `subprocess`, Dateizugriff und rohem Chrome-DevTools-Protokoll).
- Es gibt **keine Hooks**: `register_new_step_callback`, `on_step_start/end`, `pause()`, `resume()` sind in 1.0 entfernt.
  Eine Freigabe „vor dem Klick“ lässt sich in Browser Use selbst nicht erzwingen.
- `allowed_domains` ist laut Hersteller „a guard for an aligned model, not a sandbox“: Unteranfragen einer Seite und
  das eigene Netz des Python-Codes werden nicht gefiltert. `sensitive_data` ist ausdrücklich „not a sandbox“.
- Der Hersteller rät, unvertraute Aufgaben in Container oder VM auszuführen.

Verwendet werden nur öffentliche Schnittstellen: `Agent(task, llm=, browser=, web_search=False, highlight_actions=False,
extend_system_message=)`, `Browser(cdp_url=, allowed_domains=, block_ip_addresses=True)`, `run_sync(max_steps=, timeout=)`,
`final_result()`, `urls()`. Klicken und Tippen macht das Modell mit den dokumentierten Helfern (`new_tab`, `click_at_xy`,
`type_text`, `js`, `cdp`). Es wird nichts aus dem Repository kopiert.

## Aufbau: Grenzen außerhalb von Browser Use

```
Jarvis (unbeschränkt)        Sandbox A: Agent + sein Code            Sandbox B: Wegwerf-Chrome
├─ LLM-Relay  ◄───────────── nur Relay-Port und CDP-Port ──────────► CDP-Port
└─ Filterproxy ◄──────────────────────────────────────────────────── nur Proxy-Port ──► Internet (gefiltert)
```

| Grenze | Umsetzung | Wo erzwungen |
| --- | --- | --- |
| Aus per Standard | `JARVIS_BROWSER_AGENT=an`; Interaktion zusätzlich `JARVIS_BROWSER_INTERAKTION=an` | `browser_agent.tools()` |
| Kein eigenes Netz für Modell-Code | Sandbox A erlaubt nur Loopback-Ports von Relay, CDP und vier vorab festgelegten internen Ports; kein DNS, kein UDP, kein Internet, nicht Jarvis (8080/8766) | macOS `sandbox-exec` |
| Chrome nur über den Filter | Sandbox B erlaubt Chrome ausgehend nur den Proxy-Port; `--proxy-server`, `--proxy-bypass-list=<-loopback>`, kein eigenes DNS, kein QUIC | `sandbox-exec` + Chrome-Schalter |
| Nur erlaubte Domains | Proxy verbindet nur zu Hosts der Liste (und Unterdomains), nur Port 80/443, nie zu IP-Adressen | `browser_netz.Netzregeln` |
| Keine lokalen Ziele | Proxy löst selbst auf und verbindet zur geprüften Adresse; Loopback, private Netze, Link-Local/Metadaten (169.254.169.254), CGNAT, NAT64/6to4/Teredo gesperrt (auch bei DNS-Rebinding) | `browser_netz.ip_oeffentlich` |
| Nichts mit Außenwirkung ohne Freigabe | Proxy sieht HTTPS ein (Zertifikat nur für diesen Lauf) und sendet nur GET/HEAD/OPTIONS. POST/PUT/PATCH/DELETE, WebSockets und Downloads gehen nicht hinaus | `browser_netz.Filterproxy` |
| Kein Schlüssel im Agenten | Anthropic-Schlüssel bleibt im Jarvis-Prozess. Der Agent bekommt ein Einmal-Kennwort für das Relay; das Relay lässt nur `POST /v1/messages` für das festgelegte Modell durch, höchstens 95 Anfragen | `browser_netz.LlmRelay` |
| Keine Benutzerdateien | Beide Sandboxen sperren `/Users`, `/Volumes` und `/private/tmp` (außer dem Wegwerfordner) | `sandbox-exec` |
| Wegwerfprofil | frischer, unsichtbarer Chrome je Lauf mit eigenem Profil im Wegwerfordner; nie ein Chrome-Profil des Nutzers, nie die Browser-Use-Cloud, keine `sensitive_data` | `chrome_argumente()` |
| Aufräumen | Prozessgruppen von Agent und Chrome werden beendet, Wegwerfordner und Laufzertifikat gelöscht – auch nach Fehler und Zeitlimit | `ausfuehren()` (`finally`) |
| Grenzen | 25 Schritte, 240 Sekunden; Ergebnis ist als fremder Inhalt markiert | `browser_agent.py` |

Ohne Sandbox, ohne Chrome oder ohne das Paket `cryptography` startet der Agent nicht (kein stiller Betrieb ohne Grenzen).

## Zwei Modi, drei Freigaben

1. **`browser_recherche` – nur lesen.** Freigabe-Karte „Browser-Recherche starten (nur lesen)“. Der Filter sendet nichts
   mit Außenwirkung und hält nichts fest.
2. **`browser_interaktion` – klicken und eingeben** (eigener Schalter, eigene Karte „Browser-Interaktion starten (klicken
   und eingeben)“). Der Agent darf Links, Tabs und Bedienelemente anklicken und Text in Felder eingeben. Löst er dabei
   etwas mit Außenwirkung aus (Formular absenden, Nachricht senden, veröffentlichen, löschen, Konto ändern, kaufen,
   bezahlen, hochladen), **sendet der Filter es nicht**, sondern hält die Anfrage fest (höchstens 20 je Lauf, je bis 1 MB).
3. **`browser_absenden` – gesonderte Freigabe je Aktion.** Die Karte „Im Browser absenden“ zeigt Aktion und Methode,
   Website, Adresse und die zu sendenden Daten (Formularfelder, JSON oder Dateinamen). Erst nach dieser Freigabe sendet
   Jarvis genau diese Anfrage: Byte für Byte, einmal, mit denselben Netzregeln. Festgehaltene Aktionen liegen nur im
   Arbeitsspeicher und verfallen nach 15 Minuten oder einem Neustart.

Freigeben kann nur der Nutzer (Klick oder ein „Ja“ in einem späteren Zug, `approvals.py`). Das Modell kann nicht im
selben Zug freigeben, Hintergrund-Aufträge nie. Inhalte einer Webseite sind fremder Inhalt: Sie können keine Freigabe
erteilen und die Aufgabe nicht ändern – und selbst ein getäuschtes Modell kann nichts senden, weil der Filter außerhalb
seiner Reichweite liegt.

**Nicht angeboten:** angemeldete Browserprofile, gespeicherte Zugangsdaten, Anmeldungen, Konten, Zahlungen. Aufgaben mit
solchen Wörtern lehnt auch der Interaktionsmodus vor der Vorlage ab. Grund: Sobald ein Lauf eine angemeldete Sitzung
hätte, könnte modellgeschriebener Code deren Cookies lesen und über erlaubte GET-Anfragen nutzen. Ein eigenes Verfahren
für angemeldete Profile gibt es bewusst nicht. Sichere Alternative für alles mit Anmeldung: `jarvis/browser.py`
(browser-harness) steuert den echten Chrome mit Einzelfreigabe je Klick und ohne modellgeschriebenen Code.

## Geprüft am 12.11.2026 (lokal, ohne Konto, ohne API-Kosten)

`tests/test_browser_agent_e2e.py` startet das echte Browser Use 1.0.2 mit einem Skript als Modell (Pydantic AI
`FunctionModel`), den echten Wegwerf-Chrome und beide Sandboxen gegen eine lokale HTTPS-Testseite:

- Link anklicken und Text eingeben funktionieren über die Helfer von Browser Use.
- Das Absenden des Formulars und ein `fetch`-POST kommen **nicht** bei der Testseite an; beide stehen als festgehaltene
  Aktionen bereit. Nach Freigabe der ersten geht genau diese eine Anfrage hinaus, die zweite bleibt ungesendet.
- Aus dem Modell-Code scheitern: Internet, DNS, Verbindung zu Jarvis (8080), zur Brücke (8766), zum Filterproxy, zur
  Testseite direkt, `http_get`, Lesen und Schreiben im Benutzerverzeichnis. In der Umgebung steht kein Schlüssel.
- Über den Browser scheitern: Loopback, `localhost:8080`, Metadaten-Adresse, fremde Domain, `file://`.

## Restrisiken (bleiben)

- **GET mit Nebenwirkung:** Lesende Aufrufe an erlaubte Domains gehen durch. Eine Seite, die per GET etwas auslöst
  (z. B. ein Abmelde- oder Bestätigungslink), wird nicht aufgehalten. Formulare mit `method=get` (Suchfelder) gelten als
  Navigation; eingegebener Text steht dann in der Adresse an eine erlaubte Domain.
- **Unterdomains:** Eine erlaubte Domain schließt ihre Unterdomains ein. Bei Sammeldomains (z. B. `github.io`) heißt
  das: alle Seiten dort.
- **Festgehaltene Anfrage statt Browser-Sitzung:** Nach dem Lauf ist der Browser weg. Mehrstufige Abläufe nach dem
  Absenden (Bestätigungsseiten, Einmal-Token, die verfallen) funktionieren deshalb nicht immer; die Antwort der Website
  kommt als Text zurück.
- **Seiten, die POST zum Lesen brauchen** (manche Suchen, GraphQL) funktionieren im Recherchemodus nicht.
- **CDP-Port:** Der Wegwerf-Chrome öffnet einen Debugging-Port ohne Anmeldung auf 127.0.0.1 (zufälliger Port, nur für
  die Dauer des Laufs, Profil ohne Anmeldungen). Andere Programme desselben Benutzers könnten sich in dieser Zeit
  verbinden. Dasselbe gilt für Proxy- und Relay-Port (das Relay verlangt das Einmal-Kennwort).
- **Relay-Kennwort im Agenten-Prozess:** Der Modell-Code läuft als Kindprozess des Agenten. Könnte er das Kennwort
  lesen, wären höchstens die restlichen LLM-Anfragen dieses Laufs betroffen, nie der echte Schlüssel.
- **`sandbox-exec`** gilt bei Apple als veraltet. Fällt es weg, startet der Agent nicht mehr.
- **Chrome mit `--no-sandbox`** innerhalb der äußeren Sandbox; ein Ausbruch aus dem Renderer landet in Sandbox B.
- **Feste Loopback-Ports:** Browser Use bindet intern Port 0. Der Runner gibt stattdessen vier vorab erlaubte Ports aus
  (`feste_loopback_ports`). Ändert Browser Use das, scheitert der Start (kein offener Loopback).
- Die Wortlisten gegen unerwünschte Aufgaben sind Filter, kein Beweis. Die technischen Grenzen oben tragen die Last.

## Einrichten und einschalten

```bash
uv venv --python 3.12 ~/.jarvis/venvs/browser-use
uv pip install --python ~/.jarvis/venvs/browser-use/bin/python 'browser-use==1.0.2'
```

`JARVIS_BROWSER_AGENT=an` (und für Klicken/Eingeben zusätzlich `JARVIS_BROWSER_INTERAKTION=an`) in der Shell oder
`"browser_agent": "an"`, `"browser_interaktion": "an"` in `~/.jarvis.json`, dann `./jarvis-start.sh neustart`.
Den Zustand zeigt „Verbindungen“ im Dashboard. Durchstich prüfen:
`JARVIS_TEST_ECHTER_NUTZER=$USER pytest tests/test_browser_agent_e2e.py`.
