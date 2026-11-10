# Audio-Streaming und Klatschen/Schnipsen (Etappe 5 des Entwicklungsplans)

Stand: 18.10.2026.

## 5a – Sprachausgabe als Strom

- **ElevenLabs** liefert jetzt **PCM 22,05 kHz in 200-ms-Stücken** (`/text-to-speech/{id}/stream`,
  `output_format=pcm_22050`) statt eines ganzen MP3 pro Satz (`jarvis/tts.py: stream`).
- **Python-Brücke** `/internal/tts/stream`: gibt jedes Stück sofort weiter; begrenzter Puffer (8 Stücke ≈ 1,6 s)
  als Gegendruck; Zeitlimits (15 s ohne Stück, 45 s je Satz); legt Java auf (STOP/Barge-in), wird auch die
  Anbieter-Verbindung geschlossen. Rückfall vor dem ersten Stück: kostenlose Stimme als ganzes MP3.
  Die alte Route `/internal/tts` (ganzer Satz) bleibt für die native Oberfläche.
- **Java** (`TtsChunk`, `PythonBridgeTtsProvider.stream`): Stücke auf ganze 16-bit-Samples ausgerichtet, nur
  bekannte Formate; zwei Sätze werden parallel vertont, ausgeliefert wird streng in Satzreihenfolge.
- **Browser** (`audio.ts`, `protocol.ts`): PCM-Stücke werden direkt lückenlos eingeplant (kein Dekodieren);
  Puffer auf 96 Stücke erhöht (vorher 8 – hätte gestreamte Sätze abgeschnitten). STOP leert alles wie bisher.
- **Aussprache** (`jarvis/speech.py`, nur für die Vertonung, angezeigter Text unverändert): Uhrzeiten, Datum,
  Geld (auch $3,000 englisch), Prozent, Telefonnummern, Abkürzungen, Links/Markdown, Lexikon für Namen und Firmen
  (`~/.jarvis/aussprache.json`, z. B. `{"Guliver": "Gulliwer"}`).
- **Messwerte:** Brücke protokolliert je Satz `erster_chunk_ms`/`gesamt_ms` (`events.jsonl`, Art `tts_stream`);
  Java zeigt im System-Bereich TTS-Median und p95 (erstes Stück je Satz); der Browser schreibt „Erster Ton nach … ms“.
- **Gemessen (echt):** erstes Stück 124–203 ms (Median 160 ms) statt ~320 ms für das ganze MP3; Live-Test über
  Brücke, Java und Browser: alle Sätze gestreamt. Die Wartezeit bis zum ersten Ton wird jetzt vom Nachdenken des
  Modells bestimmt, nicht mehr von der Stimme. Echo-Unterdrückung/Rauschfilter/AGC im Browser waren schon aktiv.

## 5b – Klatschen oder Schnipsen öffnet Jarvis

**Standard: AUS.** Einschalten nur über das Setup (ausdrückliches Ja im Terminal).

- `jarvis/activation/`: `capture` (Mikrofon -> Ringpuffer im RAM, 4 s, Callback kopiert nur), `detector`
  (Impuls-Vorfilter -> YAMNet nur für Kandidaten -> Entscheidung), `service` (Zustände, Übergabe),
  `launcher` (öffnen/fokussieren/starten), `menubar` (Menüleiste), `settings`, `__main__` (Befehle).
- **Modell:** YAMNet float32 (MediaPipe, SHA-256 geprüft) über **LiteRT 2.3.0** – kein TensorFlow, < 1 ms je
  Fenster. Download nur beim Setup nach `~/.jarvis/models/`, danach offline.
- **Entscheidung:** Klassengruppen (Klatschen: Clapping/Hands/Slap/Applause; Schnipsen: Finger snapping) müssen
  die **kalibrierte** Schwelle UND einen Abstand zur stärksten Gegenklasse schaffen (Sprache, Tippen, Tastatur,
  Klicken, Klopfen, Tür, Musik, …). Kein fester Lautstärkewert. Ein Ereignis je Impuls (Dedupe), Cooldown 3 s,
  Doppelmodus optional (`mode: "double"`).
- **Wirkung nur öffnen:** gesperrter Bildschirm -> nichts; Jarvis läuft und Tab offen -> Tab nach vorn (Safari,
  Chrome, Arc, Brave, Edge); kein Tab -> Adresse einmal öffnen (höchstens einmal pro 60 s); Jarvis aus -> fester
  Starter `jarvis-start.sh hintergrund` mit Singleton-Sperre und Bereitschaftsprüfung. Kein Auftrag, keine Freigabe,
  keine Identitätsprüfung (Klatschen aus dem Lautsprecher oder von anderen ist nicht unterscheidbar).
- **Mikrofon-Übergabe:** Spricht Jarvis oder läuft ein Gespräch (Brücke `/health` -> `audio`), gibt die Bereitschaft
  das Mikrofon frei und übernimmt es erst 5 s danach wieder; zusätzlich wird ein Treffer verworfen, wenn Jarvis zum
  Entscheidungszeitpunkt spricht. Rohaudio wird nie gespeichert, transkribiert oder hochgeladen.
- **Sichtbar:** Menüleisten-Symbol `J ●` bereit, `J ‖` pausiert (Mikrofon frei), `J ◐` Gespräch läuft, `J !`
  Problem (kein Gerät, kein Signal/keine Freigabe, Modell fehlt), mit Pause/Fortsetzen, Jarvis öffnen, Beenden.
- **Autostart:** Die Start-App startet die Bereitschaft mit, wenn sie eingeschaltet ist (sonst beendet sie sich sofort).
- Einstellungen `~/.jarvis/aktivierung.json`: `enabled`, `triggers`, `mode`, `cooldown_ms`, `input_device`,
  `class_thresholds`, `margin`, `calibration_profile`, `foreground_policy`, `start_listening_after_open` (false),
  `confirmation_tone` (false, noch ohne Ton).

### Befehle

```
cd ~/Desktop/jarvis && ~/.jarvis/venvs/v3/bin/python -m jarvis.activation setup    # Opt-in + Kalibrierung
cd ~/Desktop/jarvis && ~/.jarvis/venvs/v3/bin/python -m jarvis.activation status
cd ~/Desktop/jarvis && ~/.jarvis/venvs/v3/bin/python -m jarvis.activation aus
```

## Tests (18.10.2026)

- Python 220 bestanden, 3 übersprungen (u. a. `tests/test_audio_stream.py`: Aussprache, PCM-Stücke, Cache,
  Abbruch schließt Anbieter, Rückfall; `tests/test_activation.py`: Vorfilter, Entscheidung, Dedupe/Cooldown/Doppel,
  Kalibrierung, Übergabe bei Sprache, Unterdrückung, Pause, Gerätefehler, stummes Signal, Modell fehlt, Standard aus,
  nur lokale Adresse, Sperrbildschirm, Fokus/Öffnen/Popup-Schutz, fester Starter, Singleton).
- Java 26/26 (`StreamingTtsTest`: Reihenfolge paralleler Sätze, Formatprüfung, Ausrichtung, Abbruch bis zur Brücke).
- Dashboard 44/44, Typprüfung, Build.

## Ehrlich offen (NOT_RUN)

- **Echte Erkennungsleistung** (Treffer und Fehlalarme mit deinem Klatschen/Schnipsen, Tippen, Tür, Musik,
  Lautsprecher, Abstand, Raum): erst mit der Kalibrierung an deinem Mac messbar. Ein synthetischer Testknall wurde von
  YAMNet nur schwach als „Hands/Slap“ erkannt – die Schwellen müssen aus echten Beispielen kommen.
- Geräteverlust/-wechsel, Entzug der Mikrofonfreigabe, Ruhezustand/Aufwachen: in Software simuliert, nicht am Gerät.
- Bestätigungston: Einstellung vorhanden, Ton noch nicht umgesetzt (standardmäßig aus).
