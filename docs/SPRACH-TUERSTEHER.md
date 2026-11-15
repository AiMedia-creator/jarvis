# Sprach-Türsteher (TypeSafe)

Stand 15.11.2026. Code: `jarvis/sprachpruefung.py` (Fragen, Schwellen, Entscheidungstabelle), `Room.java`
(vorläufiges Reinreden, Aktionen), `dashboard/src/audio.ts` (leiser stellen statt stoppen).

## Was er tut

Jede gesprochene Äußerung im Command Center (Studio-Modus) wird nach der Spracherkennung geprüft. Jev von TypeSafe
beantwortet sechs Ja/Nein-Fragen mit Wahrscheinlichkeiten; die Entscheidung trifft der Code:

| Urteil (über 0,7 = ja, unter 0,3 = nein) | Ergebnis |
|---|---|
| kein verständlicher Satz (Störgeräusch, Echo) | ignorieren |
| Jarvis antwortet gerade und du willst nur, dass er aufhört | stoppen, keine neue Antwort |
| nicht sicher verständlich (Unklar-Band) | ignorieren, Hinweis „bitte nochmal sagen“ |
| nicht an Jarvis gerichtet (Raum, Telefon) | ignorieren |
| klingt nach Sendung (Nachrichten, Wetter, Werbung, Film), nicht klar „nein“ | ignorieren |
| Jarvis spricht und es ist nur „mhm“, „ok“ | ignorieren, er spricht weiter |
| Jarvis spricht und unklar, ob du ihn meinst | ignorieren (Esc oder „Jarvis, …“ wirken immer) |
| sonst | antworten |

Ohne Modell gelten feste Regeln: „Jarvis, …“ am Satzanfang heißt antworten, ein bloßes „Stopp“ / „Halt“ während er
spricht heißt stoppen. Ein Satzrest, der zu einer schon laufenden Frage gehört, wird nie allein verworfen.

**Reinreden:** Ist der Türsteher aktiv, wird Jarvis beim Reinreden erst nur leiser (25 %). Er stoppt, sobald die
Äußerung als an ihn gerichtet erkannt ist; sonst spricht er in voller Lautstärke weiter. Ohne Türsteher stoppt
Reinreden ihn sofort wie früher.

Im Command Center steht unter dem Sprachkreis kurz, warum etwas nicht beantwortet wurde. Die Diagnose zeigt
„Türsteher“, die Einstellungen haben einen Schalter.

## Einrichten

1. Schlüssel unter https://console.typesafe.ai/keys erstellen.
2. In `~/.zshrc` eintragen: `export TYPESAFE_API_KEY='…'` (oder `"typesafe_api_key"` in `~/.jarvis.json`).
3. `./jarvis-start.sh neustart`.

Optional: `"typesafe_sprachpruefung": "aus"` schaltet ihn ab, `"typesafe_modell"` wählt eine andere Jev-Version
(Standard `jev-1.14.0`; die Schwellen gelten für diese Version).

## Daten

An TypeSafe gehen nur die erkannte Äußerung und der letzte Satz von Jarvis (höchstens 240 Zeichen), nie CRM-,
Kalender- oder Gedächtnisdaten. Nach „Gespräch zurücksetzen“ wird nichts aus dem alten Gespräch mehr gesendet.
Ist ein externer Erkennungsdienst als `JARVIS_STT_URL` eingetragen, bekommt er keinen Kontext. TypeSafe trainiert
laut eigener Doku nicht auf Kundendaten; Zero Data Retention gibt es nur im Enterprise-Tarif.

In `~/.jarvis/events.jsonl` steht je Prüfung Ergebnis, Dauer und Wahrscheinlichkeiten, aber kein Text.

Fehler (Netz, Zeitlimit 3 s, ungültige Antwort) gelten nie als „nein“: Jarvis antwortet dann wie ohne Türsteher.

## Nicht abgedeckt

- Echtzeit-Modus (ElevenLabs): Dort erkennt und entscheidet ElevenLabs selbst; der Türsteher greift nicht.
- Die Schwellen sind Startwerte (0,7/0,3) und noch nicht mit echten Aufnahmen aus deinem Raum abgestimmt.
