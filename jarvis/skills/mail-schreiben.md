---
name: mail-schreiben
description: Eine Mail formulieren und (nach Freigabe) senden - Kundenmails, Antworten, Follow-ups.
---
1. Klaere Empfaenger, Anlass und Ziel. Fehlt die Adresse: erst HubSpot (hubspot_kontakt_suchen), dann Google-Kontakte.
2. Wenn es eine Antwort ist: den Original-Thread lesen (mail_lesen) und mit antwort_auf antworten.
3. Schreibe kurz, freundlich, direkt, im Stil aus dem Profil. Klare Betreffzeile, ein klarer naechster Schritt.
4. Erstelle die Vorlage: bei Kunden aus HubSpot hubspot_mail_senden (sendet ueber Gmail und protokolliert), sonst
   mail_senden - jeweils mit bestaetigt_vom_nutzer=false. Der Nutzer sieht die Mail als Karte.
5. Fasse kurz zusammen (an wen, Betreff, Kernaussage) und frag "Soll ich sie so senden?".
6. Nach klarem Ja: derselbe Aufruf mit exakt denselben Angaben und bestaetigt_vom_nutzer=true. Danach kurz bestaetigen.
   Bei Aenderungswuenschen: neue Vorlage erstellen.
