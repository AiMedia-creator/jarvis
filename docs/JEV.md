# Jev / TypeSafe System One

Jev ist kein Gesprächsmodell. Jarvis verwendet das bestehende Sprachmodell für Antworten und Gespräche. Jev steht als optionales Werkzeug `systemone_pruefen` für strukturierte Ja/Nein-Prüfungen über einen vorgelegten Text bereit.

## Einschalten

Den API-Schlüssel als Umgebungsvariable setzen und das Werkzeug ausdrücklich aktivieren:

```bash
export TYPESAFE_API_KEY="…"
export JARVIS_JEV=an
```

Optional lässt sich der Modellalias mit `JARVIS_JEV_MODEL` festlegen; Standard ist `jev-latest`. Schlüssel gehören in die Shell oder einen Secret-Store, nicht ins Repository oder in diese Datei. Das Werkzeug erscheint nur, wenn Opt-in und Schlüssel vorhanden sind.

Das Werkzeug `systemone_pruefen` wird nur aufgerufen, wenn der Nutzer ausdrücklich nach Jev/System One oder einem Ja/Nein-Modellcheck fragt. Es schickt den angefragten Text und eine konkrete Aussage an `POST /v1/systemone` und gibt Jevs `noul`-Wert zurück. Dieser Wert ist eine modellbasierte Einschätzung, kein Beweis und keine Freigabe für Aktionen.

Davon getrennt gibt es den TypeSafe-Sprach-Türsteher für gesprochene Gespräche. Ist er in **Einstellungen → Sprach-Türsteher** eingeschaltet und ein Schlüssel vorhanden, sendet er erkannte Äußerungen sowie höchstens einen gekürzten Ausschnitt von Jarvis' letzter Sprachausgabe zur Klassifizierung. Schalte ihn dort aus, wenn du diese Sprachprüfung nicht möchtest. Textnachrichten, Erinnerungen und Obsidian-Notizen werden durch `systemone_pruefen` nicht automatisch übertragen.

## Datenverarbeitung

TypeSafe dokumentiert, dass Eingaben nicht fürs Training genutzt werden, verarbeitet Anfragen derzeit in den USA und veröffentlicht für die normale Nutzung keine feste Aufbewahrungsfrist. Zero Data Retention kann im TypeSafe-Konto aktiviert werden. Private Inhalte nur prüfen lassen, wenn du diese Übertragung möchtest: [TypeSafe zu Nutzung und Aufbewahrung](https://help.typesafe.ai/articles/5974925716).

## Technische Referenzen

- [System One API](https://docs.typesafe.ai/api/system-one)
- [Quick start und unterstützte Entscheidungsarten](https://docs.typesafe.ai/introduction/quickstart)
- [Ankündigung von Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev)
