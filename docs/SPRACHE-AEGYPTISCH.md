# Mehrsprachig sprechen: Deutsch, Englisch und Ägyptisch

Einstellung **Sprache** (Command Center → Einstellungen, oder `JARVIS_SPRACHE` / `"sprache"` in `~/.jarvis.json`):

| Wert | Verhalten |
| --- | --- |
| `de` (Standard ohne Eintrag) | Antwortet auf Deutsch. |
| `en` | Antwortet auf Englisch. |
| `arz` – Ägyptisch | Antwortet immer auf ägyptischem Arabisch (Masri), auch bei anderssprachigen Eingaben. |
| `auto` – Automatisch | Erkennt Deutsch, Englisch oder Arabisch und antwortet in der Sprache der letzten Nachricht. |

Code: `jarvis/sprache.py` (alles an einer Stelle), angebunden in Erkennung (`v3_bridge.py`, `ears.py`), Prompts
(`agent.py`, `schnellspur.py`, `eleven_agent.py`), Stimme (`tts.py`) und Einstellungen (`echtzeit.py`, `SettingsDialog.tsx`).

## Was passiert

- **Erkennung (Whisper, lokal):** Bei „Automatisch“ erkennt das Standardmodell Deutsch, Englisch oder Arabisch. Arabisch läuft
  anschließend über das konfigurierte Arabisch-Modell (Standard `small`) mit einem ägyptischen Vorgabetext, damit Whisper
  den Dialekt schreibt statt Hocharabisch. Englisch wird mit englischem Vokabular erkannt. Seit 14.11.2026 ist auch für Deutsch `small` der
  Standard (~0,7 s je Satz statt ~0,25 s mit `base`, dafür stimmen Namen und kurze Antworten; zurück mit
  `JARVIS_WHISPER_MODEL=base`). Arabisch braucht ~1,3 s. Genauer, aber langsamer: `"whisper_model_arabisch": "large-v3"` (ebenfalls im Cache).
- **Antwort:** Der Systemprompt folgt der Sprachwahl. Bei Ägyptisch verlangt er Kairoer Alltagssprache in arabischer Schrift
  („عايز“, „دلوقتي“, „مش“, „إزاي“, „كده“), statt Hocharabisch. Englisch bleibt Englisch; in „Automatisch“ folgt Jarvis der letzten Nachricht,
  übernimmt Fachbegriffe natürlich und übersetzt nicht ungefragt. Inhalte für Dritte schreibt Jarvis auf Deutsch, sofern du keine andere Sprache verlangst.
- **Stimme:** Arabische Antworten nutzen standardmäßig Microsoft „Shakir“ (`ar-EG-ShakirNeural`); „Salma“ oder eine
  ElevenLabs-Stimme sind wählbar. Wenn in „Automatisch“ eine deutsche Microsoft-Stimme ausgewählt ist, wechselt Englisch auf
  eine englische Stimme. Deutsche Zahlen- und Zeitnormalisierung wird auf Englisch und Arabisch nicht angewandt.
- **Echtzeit (ElevenLabs):** `de`, `en` und `arz` setzen die passende Grundsprache. In `auto` startet Jarvis auf Deutsch
  und nutzt ElevenLabs-Sprachprofile samt Sprachwechsel-Werkzeug für Englisch und Arabisch. Die Echtzeitstimme muss bei
  ElevenLabs liegen; lokale Microsoft-Stimmen können dort nicht geladen werden. Für Arabisch kann eine separate ElevenLabs-Stimme eingestellt werden.
- **Oberfläche:** Arabische Zeilen laufen von rechts nach links (Schrift IBM Plex Sans Arabic, lokal gebündelt).
  „وريني برلين“ öffnet die Karte wie „Zeig Berlin“ (bekannte Städte, u. a. القاهرة, الإسكندرية, دبي).

## Geprüft

- Ägyptischer Testsatz, mit „Shakir“ vertont und zurück erkannt: `small` fast wörtlich, `base` unbrauchbar.
- Claude (Sonnet 5.5) mit dem Prompt: Antwort im Dialekt („بلاش“, „مش وقته“, „بتاعتك“), auch auf eine deutsche Frage.
- Tests: `tests/test_sprache.py`, Automatik in `tests/test_v3_bridge.py`, Anzeige in `dashboard/src/command/memory.test.tsx`.

## Obsidian

Obsidian ist die lokale, schreibgeschützte Wissensbasis für längere Projekt- und Arbeitsnotizen. Den Vault-Pfad in
`~/.jarvis.json` eintragen:

```json
{"obsidian_vault": "/absoluter/Pfad/zum/Vault"}
```

Jarvis sucht passende Markdown-Notizen mit `obsidian_suchen` und liest sie mit `obsidian_lesen`; es nennt relative
Notizpfade als Quellen und behandelt Notizinhalt als Daten. Der gesamte Vault wird nicht automatisch übertragen.
Das vorhandene SQLite-Gedächtnis bleibt für ausdrücklich gespeicherte Erinnerungen und das Gesprächsarchiv erhalten;
es wird weder migriert noch ersetzt.

## Grenzen

- Die dokumentierte Prüfung lief mit synthetischer Stimme. Echte Aussprache, Hintergrundgeräusche, Arabizi und
  Code-Switching mitten im selben Satz sind schwieriger; für Arabisch kann `large-v3` genauer, aber langsamer sein.
- Sprachbefehle für Bereiche (Nachrichten, Kalender, Märkte …) erkennt die Oberfläche weiter nur auf Deutsch; das
  Gespräch selbst funktioniert auf Ägyptisch vollständig.
