# Taktwerk-Website

Statische Landingpage ohne Build-Schritt. Zum Ansehen reicht ein beliebiger Webserver im Ordner `website/`, zum Beispiel:

```bash
python3 -m http.server 8080 --directory website
```

## Dateien

- `index.html`: die Landingpage
- `impressum.html`, `datenschutz.html`: rechtliche Seiten
- `config.js`: zentrale Werte (Buchungslink, Agenturname, bestätigte CRM-Systeme)
- `styles.css`: Gestaltung der Seite; `tokens.css` Farben, Schriften und Abstände; `base.css` Grundregeln
- `fonts/`: Wix Madefor Display und Text, selbst gehostet (SIL Open Font License)
- `images/`: Hero-Foto (Thomas Bresson, CC BY 3.0, Wikimedia Commons; Nachweis steht im Footer)

## Werte ändern

- **Buchungslink:** in `config.js` (`bookingUrl`) und per Suchen und Ersetzen in den drei HTML-Dateien, damit der Link auch ohne JavaScript stimmt.
- **Farben und Schriften:** nur in `tokens.css`.
- **Hero-Foto:** `images/` austauschen und den `<img>` im `.hero__photo` in `index.html` anpassen, Nachweis im Footer aktualisieren oder entfernen.

## Noch zu ergänzen

Felder mit gestrichelter Umrandung (`class="fill"`) auf Impressum und Datenschutz: Postleitzahl, E-Mail, Telefon, Hosting-Anbieter, ggf. Rechtsform und USt-ID. Die Datenschutzerklärung ist ein sachlicher Entwurf und sollte vor Veröffentlichung rechtlich geprüft werden.
