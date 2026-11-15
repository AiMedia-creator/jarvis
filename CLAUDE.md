# Jarvis – Hinweise für Claude Code

## TypeSafe-Skill

Das Plugin `typesafe@typesafe-ai` ist installiert. Bei Arbeit an diesem Projekt den Skill `typesafe-ai` laden, sobald
eine Änderung eine Ermessensfrage über Text, Bilder oder Zustand betrifft, zum Beispiel:

- Absichtserkennung aus Nutzeräußerungen (`dashboard/src/view/intent.ts`, Schlüsselwort- und Regex-Abgleich),
- Sortieren oder Einordnen von Leads, Anrufen und Inhalten (`jarvis/leads.py`, `jarvis/anrufe.py`),
- Stellen, an denen ein LLM-Aufruf nur eine Ja/Nein- oder Auswahl-Antwort liefern und dann geparst werden soll.

Dabei gelten die Regeln des Skills: Urteile von Jev, Entscheidungen im Code; Fragen und Schwellen an einem Ort;
API-Schlüssel nur serverseitig (`TYPESAFE_API_KEY`, nie im Dashboard). Testanfragen an die Live-API nur nach Freigabe
durch Omar, mit Budget und Hinweis, welche Daten gesendet werden. Jarvis verarbeitet persönliche Daten (CRM, Kalender,
Gespräche): solche Daten nie ohne ausdrückliche Zustimmung an TypeSafe schicken.

Stand 15.11.2026: Jarvis nutzt TypeSafe noch nicht, `TYPESAFE_API_KEY` ist nicht eingerichtet.
