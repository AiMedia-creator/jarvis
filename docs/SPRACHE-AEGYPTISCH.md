# Ägyptisch (Masri) sprechen (12.11.2026)

Einstellung **Sprache** (Command Center → Einstellungen, oder `JARVIS_SPRACHE` / `"sprache"` in `~/.jarvis.json`):

| Wert | Verhalten |
| --- | --- |
| `de` (Standard ohne Eintrag) | wie bisher |
| `arz` – Ägyptisch | Jarvis versteht Arabisch und antwortet immer auf Ägyptisch, auch auf deutsche Fragen |
| `auto` – Automatisch | Jarvis antwortet in der Sprache, in der du sprichst: Deutsch auf Deutsch, Arabisch auf Ägyptisch |

Code: `jarvis/sprache.py` (alles an einer Stelle), angebunden in Erkennung (`v3_bridge.py`, `ears.py`), Prompts
(`agent.py`, `schnellspur.py`, `eleven_agent.py`), Stimme (`tts.py`) und Einstellungen (`echtzeit.py`, `SettingsDialog.tsx`).

## Was passiert

- **Erkennung (Whisper, lokal):** Arabisch läuft über das Modell `small` (liegt schon im Cache) mit Strahlsuche und einem
  ägyptischen Vorgabetext, damit Whisper Dialekt schreibt statt Hocharabisch. Bei „Automatisch“ erkennt das
  Standardmodell die Sprache, nur Deutsch oder Arabisch zur Wahl. Seit 14.11.2026 ist auch für Deutsch `small` der
  Standard (~0,7 s je Satz statt ~0,25 s mit `base`, dafür stimmen Namen und kurze Antworten; zurück mit
  `JARVIS_WHISPER_MODEL=base`). Arabisch braucht ~1,3 s. Genauer, aber langsamer: `"whisper_model_arabisch": "large-v3"` (ebenfalls im Cache).
- **Antwort:** Der Systemprompt verlangt Kairoer Alltagssprache in arabischer Schrift („عايز“, „دلوقتي“, „مش“), Anrede
  „يا ماستر“. Namen, Firmen, Zahlen bleiben; Mails, CRM-Notizen und Termine für Dritte schreibt Jarvis auf Deutsch, außer
  du verlangst etwas anderes.
- **Stimme:** Jeder überwiegend arabische Satz geht an eine ägyptische Stimme: Microsoft „Shakir“ (`ar-EG-ShakirNeural`,
  kostenlos, wie die übrigen Microsoft-Stimmen über deren Online-Dienst). Wählbar: „Salma“ (weiblich) oder eine
  ElevenLabs-Stimme (`stimme_arabisch`). Deutsche Sätze behalten deine Stimme. Die deutsche Aussprache-Normalisierung
  (Uhrzeiten, Beträge) wird bei Arabisch übersprungen.
- **Echtzeit (ElevenLabs):** bei `arz` Agentensprache Arabisch und arabische Begrüßung. Bei `auto` bleibt dort Deutsch
  die Grundsprache; der Prompt erlaubt den Wechsel, die Erkennung bei ElevenLabs ist dann aber auf Deutsch eingestellt.
- **Oberfläche:** Arabische Zeilen laufen von rechts nach links (Schrift IBM Plex Sans Arabic, lokal gebündelt).
  „وريني برلين“ öffnet die Karte wie „Zeig Berlin“ (bekannte Städte, u. a. القاهرة, الإسكندرية, دبي).

## Geprüft

- Ägyptischer Testsatz, mit „Shakir“ vertont und zurück erkannt: `small` fast wörtlich, `base` unbrauchbar.
- Claude (Sonnet 5.5) mit dem Prompt: Antwort im Dialekt („بلاش“, „مش وقته“, „بتاعتك“), auch auf eine deutsche Frage.
- Tests: `tests/test_sprache.py`, Automatik in `tests/test_v3_bridge.py`, Anzeige in `dashboard/src/command/memory.test.tsx`.

## Grenzen

- Die Prüfung lief mit synthetischer Stimme. Echte Aussprache, Hintergrundgeräusche und Code-Switching (Deutsch und
  Arabisch im selben Satz) erkennt Whisper schlechter; dann hilft `large-v3`.
- Sprachbefehle für Bereiche (Nachrichten, Kalender, Märkte …) erkennt die Oberfläche weiter nur auf Deutsch; das
  Gespräch selbst funktioniert auf Ägyptisch vollständig.
