# Jarvis World-View (Etappe 6 des Entwicklungsplans)

Stand: 19.10.2026. Öffnen: Menü ☰ -> „Weltansicht“, in der Kommandozentrale unter „Weitere Ansichten“ oder per
Sprache („Flieg nach London“, „Zeig mir Wall Street auf der Karte“, „Öffne die Weltkarte“).

## Technik

- **MapLibre GL JS 6.12.0** (BSD-Lizenz, Open-Source-Fork von Mapbox GL JS mit gleicher Kamera-API). Bewusst statt
  Mapbox GL JS: kein Konto, kein Zugriffsschlüssel, keine Nutzungsgebühren. Der bisherige Cesium-Globus wurde ersetzt
  (kein zweiter Globus); Cesium ist entfernt, auch in der Nachrichtenansicht.
- MapLibre 6 rechnet in einem Modul-Worker; Vite bündelt ihn (`?worker&url`, `worker.format = 'es'`).
- **Stil „Cyber-Map“** (`src/world/style.ts`): dunkle Vektorkarte von OpenFreeMap (OpenMapTiles/OSM, ohne Schlüssel),
  umgefärbt (Tiefschwarz/Petrol, Straßen und Grenzen in Cyan, Amber nur für Jarvis-Ziele), Gebäude als halbtransparente
  3D-Körper mit echter Höhe, Globus-Projektion mit Atmosphäre.
- **Detailstufe:** Esri World Imagery blendet ab Zoom 10,5 ein und ist ab Zoom 14 voll sichtbar (Auto); Ebenenwahl
  Auto / Satellit / Straße. Ankunft bei Zoom 16–17,2 mit 55–62° Neigung.
- **Kinoreifer Flug** (`src/world/WorldMap.ts`): ggf. erst zurück auf den Globus, dann `flyTo` mit starkem Zoom-Bogen
  (3,2–8,5 s nach Entfernung), danach langsame Umrundung bis zur ersten Nutzereingabe; bei „Bewegung reduzieren“
  direkter Sprung. Hinweis: MapLibres `maxDuration` setzt zu lange Flüge auf 0 – daher feste Dauer.
- **HUD** (`src/views/WorldView.tsx`, `src/world/world.css`): Ziel und Hinweis, Zielkreuz, pulsierende Zielmarke,
  Live-Ticker (Breite/Länge in Grad/Minuten/Sekunden, Höhe, Zoom, Kurs, Neigung, Ebene, Online-Status), gespeicherte
  Orte (Wall Street, London City, Frankfurter Börse, Berlin Mitte, Stuttgart, Silicon Valley, Tokio, Dubai, Singapur),
  Ortssuche, Quellenangaben. Schmale Fenster: kompakte Leiste.
- **Ortssuche** über die Python-Brücke (`jarvis/geocode.py`, Java `/api/geocode`): OpenStreetMap Nominatim nach deren
  Regeln (eigene Kennung, höchstens 1 Anfrage/s, Zwischenspeicher, nur auf Enter oder Sprachbefehl).

## Ehrliche Angaben in der Ansicht

- Satellitenbild: **Archivbild, Aufnahmedatum unbekannt – kein Livebild** (Esri World Imagery, Maxar, Earthstar).
- Basiskarte: OpenFreeMap · © OpenStreetMap-Mitwirkende. Attribution unten rechts.
- Basiskarte nicht erreichbar -> „OFFLINE“, Flug mit Satellitenbild bleibt möglich.

## Tests (19.10.2026)

- Dashboard 52/52, Typprüfung, Build (`src/world/world.test.ts`: Ortserkennung, Koordinatenformat, Höhe,
  Suchtreffer-Detailstufe, Stil: Satellit über Grundflächen und unter Gebäuden/Straßen/Beschriftungen, 3D-Gebäude,
  Globus, Cyan-Straßen, Archiv-Hinweis, Ebenen; Sprachbefehle ohne Nachrichtenfragen zu stören).
- Python 220, Java (mit Paketbau) grün.
- Live im Browser: Globus mit Atmosphäre, Flug Wall Street und London (Zwischenbilder bei 1,5 / 3,5 / 6 s, Ankunft mit
  Satellit, 3D, Umrundung), Ortssuche „Eiffelturm“.

## Noch offen aus dem Plan (Abschnitt 9)

- Kamera-Ansicht: „MP4“ darf nicht als „LIVE“ erscheinen; Status LIVE/RECENT/ARCHIVE/CLIP/SIMULATED je Quelle.
- Optionale Ebenen: Sentinel-Zeitreihen, Google 3D/Street View (nur mit geprüften Nutzungsrechten), Webcams nur mit
  belegter Abdeckung und Berechtigung.
