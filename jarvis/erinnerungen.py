"""Jarvis Memory v1: dauerhafte Erinnerungen nur auf ausdrücklichen Wunsch – gezielt abgerufen, sichtbar, löschbar.

Speicherort: ``~/.jarvis/memory.sqlite3``, Tabelle ``facts`` (mit Versionsverlauf ``fact_versions``). Getrennt davon:
der Chatverlauf (Tabelle ``episodes``) und hochgeladene Dateien (nur Arbeitsspeicher, ``anhaenge.py``).

Regeln (im Code, nicht nur im Prompt):
- Gespeichert wird nur, wenn du es ausdrücklich sagst („Merk dir, dass …“, „Notier dir …“, „Speicher dir …“) oder im
  Gedächtnis-Center selbst anlegst. Aus normalen Gesprächen leitet Jarvis nichts dauerhaft ab.
- Unklare Fälle (Frage, Vermutung, Widerspruch zu einer älteren Erinnerung) werden als VORSCHLAG abgelegt und erst
  nach deinem „Ja“ verwendet. Schlägt das Modell selbst etwas vor (wissen_speichern), gilt dasselbe.
- Nur zur Frage passende Erinnerungen (Volltexttreffer, höchstens ``MAX_RECALL``) gehen ins Modell, nie der ganze
  Speicher. Mit ``erinnerungen_an_cloud = aus`` gehen an Cloud-Anbieter (Anthropic, Moonshot) gar keine.
- Chat-Befehle werden lokal beantwortet, ohne Modellaufruf: Erinnerungen zeigen, einzeln oder alle löschen.
"""

from __future__ import annotations

import re
import time

from . import config

MAX_RECALL = 5
# Antworten auf Rückfragen werden wortweise ausgewertet, nicht über den Satzanfang: „Ja, aber bitte nicht“ ist kein Ja.
# Regel: Jede Verneinung oder Einschränkung schlägt jedes Ja. Im Zweifel passiert nichts und Jarvis fragt erneut.
_JA_STARK = frozenset("ja jo jep jap jawohl yes ok okay genau richtig passt einverstanden".split())
_JA_SCHWACH = frozenset("bitte gerne gern klar sicher natürlich natuerlich unbedingt".split())
_NEIN = frozenset("nein nee ne nö noe no nicht nichts nie niemals nix kein keine keinen keinesfalls stopp stop halt abbrechen "
                  "abbruch verwerfen vergiss lassen lass falsch".split())
_ZWEIFEL = frozenset("aber warte moment oder vielleicht eventuell später spaeter erst erstmal sondern außer ausser nur "
                     "jein hm hmm".split())
_FUELL = frozenset("das es so doch mach mache machen tu tue danke master jarvis wirklich endgültig endgueltig".split())
_WORT_LOESCHEN = frozenset("löschen lösche lösch loeschen loesche loesch entfernen entferne die sie erinnerung".split())
_WORT_SPEICHERN = frozenset("speichern speichere speicher merken merke merk dir trotzdem".split())
_WORT_ALLE = frozenset("alle sämtliche saemtliche erinnerungen meine".split())
_TOKEN = re.compile(r"[a-zäöüß]+")
MAX_ANTWORT_WORTE = 6    # längere Sätze sind keine Antwort auf die Rückfrage („Ja, und wie wird das Wetter morgen …?“)
RUECKFRAGE_GILT_S = 120  # eine Löschrückfrage verfällt; ein spätes „Ja“ löscht nichts mehr
ALLE_LOESCHEN_SATZ = "Ja, alle löschen"


def deute_antwort(text: str, art: str = "loeschen") -> str | None:
    """Deutet die Antwort auf eine Rückfrage: ``"ja"``, ``"nein"``, ``"unklar"`` oder ``None`` (keine Antwort darauf).

    ``"ja"`` gibt es nur, wenn JEDES Wort zustimmend oder ein zur Aktion passendes Füllwort ist. Für ``alle_loeschen``
    muss die Antwort die Aktion selbst nennen („Ja, alle löschen“); ein bloßes „Ja“ reicht dort nicht.
    """
    worte = _TOKEN.findall(str(text or "").lower())
    if not worte or len(worte) > MAX_ANTWORT_WORTE:
        return None
    menge = set(worte)
    stark, schwach = menge & _JA_STARK, menge & _JA_SCHWACH
    if menge & _NEIN:
        if stark:
            return "unklar"  # „Ja, nicht löschen“: widersprüchlich, also nichts tun und nachfragen
        # Klare Ablehnung („Nein“, „Bitte nicht“, „Nein, lass das“). Ein längerer Satz, der nur irgendwo „nicht“ enthält,
        # ist keine Antwort auf die Rückfrage – ausgeführt wird in beiden Fällen nichts.
        return "nein" if worte[0] in _NEIN or len(worte) <= 3 else None
    if menge & _ZWEIFEL:
        return "unklar"
    if not (stark or schwach):
        return None
    erlaubt = _JA_STARK | _JA_SCHWACH | _FUELL | (_WORT_SPEICHERN if art == "speichern" else _WORT_LOESCHEN)
    if art == "alle_loeschen":
        if not menge <= erlaubt | _WORT_ALLE:
            return "unklar"
        ausdruecklich = stark and menge & {"alle", "sämtliche", "saemtliche"} and menge & (_WORT_LOESCHEN - {"die", "sie", "erinnerung"})
        return "ja" if ausdruecklich else "unklar"
    if menge <= erlaubt:
        return "ja"
    # Beginnt wie eine Antwort, enthält aber Unbekanntes („Ja morgen“, „Ja alle löschen“ bei einer Einzelrückfrage).
    return "unklar" if worte[0] in _JA_STARK and len(worte) <= 4 else None


_SAVE = re.compile(
    r"^\s*(?:bitte\s+)?(?:merk(?:e)?\s+dir|notier(?:e)?\s+dir|speicher(?:e)?\s+dir)\b(?:\s+bitte)?\s*[,:]?\s*"
    r"(?:dass\s+|folgendes\s*:?\s*)?(?P<inhalt>.*?)\s*[.!]*\s*$", re.I | re.S)
_LIST = re.compile(
    r"^\s*(?:(?:zeig|nenn|list)\w*\s+(?:mir\s+)?(?:bitte\s+)?(?:alle\s+)?(?:meine\s+)?(?:gespeicherten\s+)?erinnerungen"
    r"|meine\s+erinnerungen|was\s+(?:wei(?:ß|ss)t|hast\s+du)\s+du\s+(?:alles\s+)?(?:über|ueber)\s+mich(?:\s+gespeichert)?)"
    r"\s*[?.!]*\s*$", re.I)
_DELETE_NR = re.compile(r"^\s*(?:l(?:ö|oe|o)sch\w*|vergiss|entfern\w*)\s+(?:die\s+)?erinnerung\s+(?:nr\.?\s*|nummer\s+)?(?P<nr>\d{1,3})\s*[.!]*\s*$", re.I)
_DELETE_ALL = re.compile(r"^\s*(?:l(?:ö|oe|o)sch\w*|vergiss|entfern\w*)\s+(?:bitte\s+)?(?:alle|sämtliche|saemtliche)\s+(?:meine\s+)?erinnerungen\s*[.!]*\s*$", re.I)
_FORGET_TEXT = re.compile(r"^\s*vergiss\s*,?\s*dass\s+(?P<inhalt>.+?)\s*[.!]*\s*$", re.I | re.S)
_REFERENCE_ONLY = re.compile(r"^(das|dies|dieses|es|das hier|das da|das gerade|das von eben|den letzten punkt|oben)$", re.I)
_HEDGE = re.compile(r"\b(vielleicht|eventuell|evtl\.?|glaube|vermutlich|wahrscheinlich|könnte|koennte|ich denke)\b", re.I)


def cloud_allowed(provider_id: str) -> bool:
    """Dürfen abgerufene Erinnerungen an diesen Anbieter gehen? Lokal (Ollama) immer, Cloud nur mit Einstellung „an“."""
    if provider_id == "ollama":
        return True
    return str(config.get("erinnerungen_an_cloud", "an") or "an").strip().lower() not in {"aus", "false", "0", "nein", "off"}


def relevant(store, query: str, limit: int = MAX_RECALL) -> list[dict]:
    """Nur zur Frage passende, bestätigte Erinnerungen (echte Volltexttreffer, keine „neuesten Einträge“ als Ersatz)."""
    if not str(query or "").strip():
        return []
    return store.search(query, limit, strict=True)["facts"][:limit]


def _short(text: str, n: int = 160) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


class Commands:
    """Lokale Chat-Befehle und Rückfragen. Zustand je Gespräch (offene Rückfrage, zuletzt gezeigte Liste)."""

    def __init__(self, store):
        self.store = store
        self.pending: dict | None = None   # {"art": "speichern"|"loeschen"|"alle_loeschen", ...}
        self.listed: list[str] = []        # IDs der zuletzt gezeigten Liste (für „Lösche Erinnerung 3“)

    def propose(self, fact: dict) -> None:
        """Ein Vorschlag wartet auf Ja/Nein (z. B. vom Modell über wissen_speichern)."""
        self.pending = {"art": "speichern", "id": fact["id"], "inhalt": fact["content"]}

    def handle(self, text: str) -> str | None:
        """Antwort, wenn die Eingabe ein Erinnerungsbefehl oder die Antwort auf eine Rückfrage ist; sonst None."""
        raw = str(text or "").strip()
        if not raw:
            return None
        pending, self.pending = self.pending, None
        if pending and pending["art"] != "speichern" and time.monotonic() - pending.get("zeit", 0.0) > RUECKFRAGE_GILT_S:
            pending = None  # verfallene Löschrückfrage: ein spätes „Ja“ gehört nicht mehr dazu
        befehl = any(m.match(raw) for m in (_LIST, _DELETE_ALL, _DELETE_NR, _FORGET_TEXT, _SAVE))
        if pending and not befehl:  # ein neuer Befehl („Merk dir, dass ich nicht rauche“) ist keine Antwort
            deutung = deute_antwort(raw, pending["art"])
            if deutung == "unklar":
                self.pending = pending  # nichts ausführen, Rückfrage bleibt offen
                return self._ask_again(pending)
            if deutung:
                return self._answer(pending, deutung == "ja")
            # Keine Antwort auf die Rückfrage: nichts wird gelöscht; ein Vorschlag bleibt unbestätigt im Gedächtnis-Center.
        if _LIST.match(raw):
            return self._list()
        match = _DELETE_ALL.match(raw)
        if match:
            count = self.store.stats()["facts"]
            if not count:
                return "Es sind keine Erinnerungen gespeichert."
            self.pending = {"art": "alle_loeschen", "anzahl": count, "zeit": time.monotonic()}
            return (f"Soll ich wirklich alle {count} Erinnerungen endgültig löschen? Das lässt sich nicht rückgängig "
                    f"machen. Antworte zum Löschen genau mit „{ALLE_LOESCHEN_SATZ}“, sonst mit „Nein“.")
        match = _DELETE_NR.match(raw)
        if match:
            index = int(match.group("nr")) - 1
            if not self.listed:
                return "Sag zuerst „Zeig meine Erinnerungen“ – dann kann ich eine Nummer löschen."
            if not 0 <= index < len(self.listed):
                return f"Die Liste hat nur {len(self.listed)} Einträge."
            return self._ask_delete([self.listed[index]])
        match = _FORGET_TEXT.match(raw)
        if match:
            found = relevant(self.store, match.group("inhalt"), 3)
            if not found:
                return "Dazu habe ich keine gespeicherte Erinnerung gefunden."
            return self._ask_delete([found[0]["id"]])
        match = _SAVE.match(raw)
        if match:
            return self._save(match.group("inhalt"))
        return None

    # --- Speichern ---

    def _save(self, content: str) -> str:
        content = re.sub(r"(?i)^(?:bitte\s+)?als\s+\w+\s*:\s*", "", (content or "").strip()).strip()
        if not content or _REFERENCE_ONLY.match(content.rstrip(".!? ")):
            return "Was genau soll ich mir merken? Sag es bitte als ganzen Satz, z. B. „Merk dir, dass ich Tee trinke.“"
        reason = None
        if "?" in content:
            reason = "Das klingt nach einer Frage."
        elif _HEDGE.search(content):
            reason = "Das klingt unsicher."
        elif len(content.split()) < 2:
            reason = "Das ist sehr knapp."
        try:
            if reason is None:
                saved = self.store.remember(content, "note", "user", client="chat")
                if saved.get("duplicate"):
                    return f"Das habe ich schon gespeichert: „{_short(saved['content'])}“."
                if not saved.get("conflicts"):
                    return (f"Gemerkt: „{_short(content)}“. Ansehen, ändern oder löschen kannst du es im "
                            "Gedächtnis-Center oder mit „Zeig meine Erinnerungen“.")
                # Widerspruch: neu gespeichert, aber erst nach Bestätigung verwendet.
                self.store.mark_proposal(saved["id"])
                self.pending = {"art": "speichern", "id": saved["id"], "inhalt": content}
                return (f"Das widerspricht einer älteren Erinnerung: „{_short(saved['conflicts'][0]['content'])}“. "
                        f"Soll ich „{_short(content)}“ trotzdem speichern? Antworte mit „Ja“ oder „Nein“.")
            saved = self.store.remember(content, "note", "user", client="chat", consent="vorschlag")
        except ValueError:
            return "Das speichere ich nicht: Zugangsdaten, Passwörter, Codes oder Bankdaten bleiben draußen."
        self.pending = {"art": "speichern", "id": saved["id"], "inhalt": content}
        return f"{reason} Soll ich mir „{_short(content)}“ trotzdem merken? Antworte mit „Ja“ oder „Nein“."

    # --- Anzeigen und Löschen ---

    def _list(self) -> str:
        facts = self.store.list_facts(30)
        self.listed = [f["id"] for f in facts]
        if not facts:
            return "Ich habe noch keine Erinnerungen über dich gespeichert. Sag „Merk dir, dass …“, um eine anzulegen."
        lines = [f"{i}. {_short(f['content'], 200)}{' (Vorschlag, unbestätigt)' if f['consent'] == 'vorschlag' else ''}"
                 for i, f in enumerate(facts, 1)]
        more = self.store.stats()["facts"] - len(facts)
        tail = f"\n… und {more} weitere im Gedächtnis-Center." if more > 0 else ""
        return ("Gespeicherte Erinnerungen:\n" + "\n".join(lines) + tail +
                "\nLöschen: „Lösche Erinnerung 2“ oder „Lösche alle Erinnerungen“. Bearbeiten im Gedächtnis-Center.")

    def _ask_delete(self, ids: list[str]) -> str:
        fact = self.store.get_fact(ids[0])
        if fact is None:
            return "Diese Erinnerung gibt es nicht mehr."
        self.pending = {"art": "loeschen", "ids": ids, "inhalt": fact["content"], "zeit": time.monotonic()}
        return f"Soll ich „{_short(fact['content'])}“ endgültig löschen? Antworte mit „Ja“ oder „Nein“."

    def _ask_again(self, pending: dict) -> str:
        """Mehrdeutige Antwort: nichts tun und dieselbe Frage noch einmal stellen."""
        if pending["art"] == "alle_loeschen":
            return (f"Ich habe nichts gelöscht. Wenn wirklich alle {pending['anzahl']} Erinnerungen weg sollen, antworte "
                    f"genau mit „{ALLE_LOESCHEN_SATZ}“. Sonst antworte mit „Nein“.")
        if pending["art"] == "loeschen":
            return (f"Das war nicht eindeutig, ich habe nichts gelöscht. Soll ich „{_short(pending.get('inhalt', ''))}“ "
                    "endgültig löschen? Antworte nur mit „Ja“ oder „Nein“.")
        return (f"Das war nicht eindeutig, ich habe nichts gespeichert. Soll ich mir „{_short(pending['inhalt'])}“ merken? "
                "Antworte nur mit „Ja“ oder „Nein“.")

    def _answer(self, pending: dict, yes: bool) -> str:
        if pending["art"] == "speichern":
            if yes:
                try:
                    self.store.confirm(pending["id"])
                except KeyError:
                    return "Der Vorschlag existiert nicht mehr."
                return f"Gemerkt: „{_short(pending['inhalt'])}“."
            self.store.forget(pending["id"])
            return "In Ordnung, ich speichere das nicht."
        if pending["art"] == "loeschen":
            if not yes:
                return "In Ordnung, nichts gelöscht."
            done = [i for i in pending["ids"] if self.store.forget(i)]
            self.listed = [i for i in self.listed if i not in done]
            return "Gelöscht." if done else "Die Erinnerung war schon gelöscht."
        if pending["art"] == "alle_loeschen":
            if not yes:
                return "In Ordnung, nichts gelöscht."
            jetzt = self.store.stats()["facts"]
            if jetzt != pending["anzahl"]:
                # Die Bestätigung galt für einen anderen Bestand: neu fragen statt mehr zu löschen als angekündigt.
                self.pending = {"art": "alle_loeschen", "anzahl": jetzt, "zeit": time.monotonic()}
                return (f"Der Bestand hat sich geändert: Es sind jetzt {jetzt} Erinnerungen. Ich habe nichts gelöscht. "
                        f"Antworte zum Löschen erneut genau mit „{ALLE_LOESCHEN_SATZ}“, sonst mit „Nein“.")
            count = self.store.forget_all()
            self.listed = []
            return f"Alle {count} Erinnerungen sind gelöscht. Chatverlauf und Dateianhänge sind davon nicht betroffen."
        return "In Ordnung."


def summary(facts: list[dict]) -> list[dict]:
    """Für die Anzeige „Erinnerung verwendet“ (ohne interne Felder)."""
    return [{"id": f["id"], "inhalt": _short(f["content"], 120)} for f in facts]
