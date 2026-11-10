# Java 21 / Spring Boot WebFlux

```bash
# Im Projekt-Hauptordner:
chmod +x start-java-demo.sh
./start-java-demo.sh
# Browser: http://localhost:8080
```

```bash
# Alternativ exakt aus java-backend/ starten:
cd java-backend
./mvnw test package
java -jar target/jarvis-java-backend-1.0.0.jar
```

Der Maven Wrapper lädt Maven selbst, wenn erforderlich. Die Standardkonfiguration bindet `127.0.0.1:8080` und liefert das Frontend aus `../dashboard/dist`. Beim Start aus einem anderen Arbeitsverzeichnis `JARVIS_STATIC_DIR` als absoluten Pfad setzen oder das Hauptordner-Startskript verwenden.

```bash
# Echter Modell-/TTS-Betrieb:
export JARVIS_DEMO=false
export ANTHROPIC_API_KEY='DEIN_SCHLUESSEL'
export ELEVENLABS_API_KEY='DEIN_SCHLUESSEL'
export ELEVENLABS_VOICE_ID='DEINE_VOICE_ID'
# Optional echter Audioeingang: lokal oder autorisierter HTTPS-Whisper-Multipart-Endpunkt.
export JARVIS_STT_URL='http://localhost:9000/v1/audio/transcriptions'
# Optional für diesen STT-Endpunkt:
# export JARVIS_STT_API_KEY='DEIN_SCHLUESSEL'
./start-java-demo.sh
```

`ANTHROPIC_MODEL` verwendet standardmäßig `claude-sonnet-5-5`. Ohne `JARVIS_STT_URL` liefert echter Mikrofonbetrieb eine klare Fehlermeldung statt vorgetäuschter Transkription. Der Demomodus erzeugt synthetischen Text und WAV-Testtöne; Demo-Audio wird ausdrücklich nicht als erkannter Nutzertext ausgegeben. Tests verwenden lokale HTTP-Doubles, keine persönlichen Schlüssel.

```bash
# Explizit berechtigte Kameraquellen aus einer JSON-Datei:
export JARVIS_CAMERAS_FILE='/absoluter/pfad/cameras.json'
# Format siehe ../cameras.example.json; Live-URLs nur HTTPS, Typ hls oder mp4.
```

```bash
# Nichtlokaler Betrieb nur hinter eigenem HTTPS-Reverse-Proxy:
export JARVIS_BIND=0.0.0.0
export JARVIS_SESSION_TOKEN='MINDESTENS_16_ZUFAELLIGE_ZEICHEN'
export JARVIS_ALLOWED_ORIGINS='https://jarvis.example.com'
java -jar target/jarvis-java-backend-1.0.0.jar --jarvis.secure-cookie=true
```

Nichtlokaler Bind erfordert einen Token und setzt Secure-Cookies standardmäßig. Bei einem HTTPS-Proxy mit Loopback-Bind zusätzlich `--jarvis.secure-cookie=true` setzen. Cookies sind HttpOnly/SameSite=Strict; externe iframe-Einbettung ist nicht vorgesehen. Exakte lokale Origins für Vite-Port 4173 sind standardmäßig erlaubt. Wildcards und fremde Origins werden abgewiesen. `/health` und `/actuator/health` sind Readiness-Endpunkte, andere Actuator-Endpunkte benötigen bei aktiviertem Auth eine Session.

Der Java-Baustein ist ein zusätzlicher Backend-Adapter. Er übernimmt keine bestehenden Python-Google-/HubSpot-Integrationen oder deren Konversationshistorie. Diese bleiben im Python-Paket nutzbar; für die Java-Anbindung die Interfaces `GenerationProvider`, `TtsProvider` und `SttProvider` im vorhandenen System implementieren bzw. mit einem authentifizierten internen Gateway verbinden. Modell-Gedankengänge werden nicht ausgelesen. Ausgabe ist satzweise vollständig dekodierbares MP3/WAV, kein vorgetäuschtes samplegenaues Provider-Realtime-Protokoll.

Audiopuffer und PCM-Aufnahmen sind begrenzt. Unterbrechungen invalidieren die Generation und leeren die Ausgabe vor der Bestätigung. Reactor-Cancellation beendet asynchrone HTTP-Anfragen. Ein `CancellationToken` kann zusätzlich einen tatsächlich dedizierten blockierenden Worker unterbrechen; niemals einen Netty-Event-Loop-Thread daran binden. Kontrollobjekte und HTTP-/JSON-Pakete benötigen Speicherallokationen; eine wörtliche Zero-Allocation-Garantie wird nicht behauptet.
