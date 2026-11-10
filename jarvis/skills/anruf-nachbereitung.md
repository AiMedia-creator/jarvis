---
name: anruf-nachbereitung
description: Nach einem Telefonat - Diktat des Nutzers in ein gespeichertes Anruf-Ergebnis, Follow-up und (nur nach Ja) CRM-Notiz und Mail-Entwurf verwandeln.
---
Ablauf nach einem Anruf (Nutzer diktiert in einem Satz oder ein paar Saetzen):
1. Projekt klaeren: projekt_liste. Gibt es nur eins, ist es gemeint. Sonst kurz nachfragen. Gibt es keins: erst projekt_speichern
   (nur Angaben, die der Nutzer nennt).
2. Fehlt etwas Wesentliches (Firma oder Name, Ergebnis), EINE kurze Rueckfrage. Nichts raten, nichts erfinden.
   Ergebnis ist genau eines von: nicht_erreicht, falsche_nummer, kein_interesse, rueckruf, interesse, termin, abschluss.
   Telefonstatus (geschaeftlich, privat, unklar) nur eintragen, wenn genannt; sonst unklar.
3. anruf_erfassen aufrufen. Das speichert nur lokal, legt bei einem naechsten Schritt einen Auftrag im Kanban an und schreibt NICHTS ins CRM.
4. Sage dem Nutzer knapp, was gespeichert ist und dass der CRM-Eintrag noch offen ist.
5. CRM-Eintrag (nur wenn das Projekt-CRM hubspot ist und der Nutzer es will): hubspot_kontakt_suchen, dann hubspot_notiz_erstellen als
   Vorlage zeigen (bestaetigt_vom_nutzer=false). Erst nach "Ja" identisch mit true. Danach anruf_crm_status auf geschrieben setzen,
   bei einer nur gezeigten Vorlage auf vorschau. Fuer gohighlevel und close gibt es noch keine Anbindung: sag das ehrlich und
   setze nichts auf geschrieben.
6. Follow-up-Mail: entwurf_erstellen (nur Entwurf, nie senden ohne ausdrueckliches Ja des Nutzers).
Vor einem Anruf: anruf_karte (Projekt, Name, Firma) zeigt Leitfaden, fruehere Anrufe, offene Follow-ups und Hinweise zur Nummer.
Zahlen (Gespraeche, Termine pro 100) nur aus anruf_kennzahlen nennen, mit Zeitraum. Kein Anruf erfasst heisst: keine Zahl.
Telefonnummern ohne klare Quelle oder mit unklarem Status nie als erlaubt darstellen; bei Unsicherheit den Nutzer entscheiden lassen.
