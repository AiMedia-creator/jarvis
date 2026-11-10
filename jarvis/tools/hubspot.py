"""HubSpot-CRM: Kontakte und Deals abfragen, Notizen, Aufgaben, E-Mails protokollieren und senden.

Zugang ueber eine "Private App" (Access Token). Das Token kommt aus der Umgebungsvariable
HUBSPOT_TOKEN oder aus ~/.jarvis.json (Schluessel "hubspot_token").
Schreibende Werkzeuge verlangen `bestaetigt_vom_nutzer=true` (nur nach einem klaren "Ja").
"""

from __future__ import annotations

import datetime
import re
from typing import Any

import requests

from .. import config
from . import Tool, ToolFailure, boolean, cached, string

API = "https://api.hubapi.com"
CONTACT_PROPS = ["firstname", "lastname", "email", "phone", "mobilephone", "company", "jobtitle",
                 "lifecyclestage", "hs_lead_status", "notes_last_updated", "lastmodifieddate"]
DEAL_PROPS = ["dealname", "amount", "dealstage", "pipeline", "closedate", "hs_is_closed",
              "hubspot_owner_id", "lastmodifieddate"]


def is_configured() -> bool:
    return bool(config.get("hubspot_token"))


# --- Basis ---------------------------------------------------------------------

def _call(method: str, path: str, *, json: Any = None, params: dict | None = None) -> dict:
    token = config.get("hubspot_token")
    if not token:
        raise ToolFailure("HubSpot ist nicht eingerichtet (HUBSPOT_TOKEN fehlt).")
    try:
        r = requests.request(method, API + path, json=json, params=params, timeout=20,
                             headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    except requests.RequestException as exc:
        raise ToolFailure(f"HubSpot nicht erreichbar: {exc}") from exc
    if r.status_code == 401:
        raise ToolFailure("HubSpot lehnt das Token ab (401). Token pruefen oder neu erzeugen.")
    if r.status_code == 429:
        raise ToolFailure("HubSpot-Limit erreicht (429). Kurz warten und nochmal versuchen.")
    if r.status_code >= 400:
        try:
            msg = r.json().get("message", r.text)
        except ValueError:
            msg = r.text
        hint = " (Es fehlt vermutlich ein Zugriffsrecht/Scope in der Private App.)" if r.status_code == 403 else ""
        raise ToolFailure(f"HubSpot-Fehler {r.status_code}: {msg[:400]}{hint}")
    return r.json() if r.content else {}


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _due_iso(value: str | None) -> str:
    """AAAA-MM-TT oder AAAA-MM-TTTHH:MM (lokale Zeit) -> UTC-ISO fuer HubSpot. Leer = morgen 9 Uhr."""
    tz = datetime.datetime.now().astimezone().tzinfo
    if not value:
        base = (datetime.datetime.now() + datetime.timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        dt = base.replace(tzinfo=tz)
    else:
        try:
            dt = datetime.datetime.fromisoformat(value)
        except ValueError as exc:
            raise ToolFailure(f"Ungueltiges Datum '{value}', erwartet AAAA-MM-TT oder AAAA-MM-TTTHH:MM") from exc
        if len(value) <= 10:
            dt = dt.replace(hour=9)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _need_confirm(ok: bool, what: str) -> None:
    if not ok:
        raise ToolFailure(f"{what} abgelehnt: Kuendige zuerst genau an, was du tun willst, "
                          "und warte auf ein klares 'Ja' des Nutzers.")


_stage_cache: dict[str, str] = {}


def _stage_labels() -> dict[str, str]:
    """Deal-Phasen-ID -> lesbarer Name (einmal laden)."""
    if not _stage_cache:
        try:
            for pipe in _call("GET", "/crm/v3/pipelines/deals").get("results", []):
                for st in pipe.get("stages", []):
                    _stage_cache[st["id"]] = f"{st['label']} ({pipe['label']})"
        except ToolFailure:
            pass  # Phasen-IDs sind dann eben unuebersetzt
    return _stage_cache


def _fmt_contact(obj: dict) -> str:
    p = obj.get("properties", {})
    name = " ".join(x for x in (p.get("firstname"), p.get("lastname")) if x) or "(ohne Namen)"
    parts = [f"id={obj['id']}", name]
    for label, key in (("Firma", "company"), ("Position", "jobtitle"), ("Mail", "email"),
                       ("Tel", "phone"), ("Mobil", "mobilephone"), ("Phase", "lifecyclestage"),
                       ("Lead-Status", "hs_lead_status")):
        if p.get(key):
            parts.append(f"{label}: {p[key]}")
    return " | ".join(parts)


def _fmt_deal(obj: dict) -> str:
    p = obj.get("properties", {})
    stage = _stage_labels().get(p.get("dealstage") or "", p.get("dealstage") or "?")
    amount = p.get("amount")
    try:
        amount = f"{float(amount):,.0f} EUR".replace(",", ".") if amount else "kein Betrag"
    except ValueError:
        pass
    close = (p.get("closedate") or "")[:10] or "kein Abschlussdatum"
    state = "geschlossen" if p.get("hs_is_closed") == "true" else "offen"
    return f"id={obj['id']} | {p.get('dealname', '(ohne Namen)')} | {amount} | Phase: {stage} | Abschluss: {close} | {state}"


# --- Lesen ---------------------------------------------------------------------

@cached(30)
def kontakt_suchen(suche: str, anzahl: int = 5) -> str:
    data = _call("POST", "/crm/v3/objects/contacts/search",
                 json={"query": suche, "limit": max(1, min(anzahl, 15)), "properties": CONTACT_PROPS})
    results = data.get("results", [])
    return "\n".join(_fmt_contact(c) for c in results) or f"Kein HubSpot-Kontakt zu '{suche}' gefunden."


@cached(30)
def deals_suchen(suche: str = "", nur_offene: bool = True, anzahl: int = 10) -> str:
    body: dict = {"limit": max(1, min(anzahl, 25)), "properties": DEAL_PROPS,
                  "sorts": [{"propertyName": "closedate", "direction": "ASCENDING"}]}
    if suche:
        body["query"] = suche
    if nur_offene:
        body["filterGroups"] = [{"filters": [{"propertyName": "hs_is_closed", "operator": "EQ", "value": "false"}]}]
    results = _call("POST", "/crm/v3/objects/deals/search", json=body).get("results", [])
    return "\n".join(_fmt_deal(d) for d in results) or "Keine passenden Deals gefunden."


def _associated_ids(from_type: str, from_id: str, to_type: str) -> list[str]:
    data = _call("GET", f"/crm/v4/objects/{from_type}/{from_id}/associations/{to_type}")
    return [str(r["toObjectId"]) for r in data.get("results", [])]


def deals_zu_kontakt(kontakt_id: str) -> str:
    ids = _associated_ids("contacts", kontakt_id, "deals")
    if not ids:
        return "Zu diesem Kontakt gibt es keine Deals."
    data = _call("POST", "/crm/v3/objects/deals/batch/read",
                 json={"properties": DEAL_PROPS, "inputs": [{"id": i} for i in ids[:25]]})
    return "\n".join(_fmt_deal(d) for d in data.get("results", []))


def notizen_lesen(kontakt_id: str, anzahl: int = 5) -> str:
    ids = _associated_ids("contacts", kontakt_id, "notes")[:max(1, min(anzahl, 15))]
    if not ids:
        return "Keine Notizen zu diesem Kontakt."
    data = _call("POST", "/crm/v3/objects/notes/batch/read",
                 json={"properties": ["hs_note_body", "hs_timestamp"], "inputs": [{"id": i} for i in ids]})
    rows = []
    for n in data.get("results", []):
        p = n.get("properties", {})
        text = re.sub(r"<[^>]+>", " ", p.get("hs_note_body") or "")
        clean = re.sub(r"\s+", " ", text).strip()[:500]
        rows.append(f"{(p.get('hs_timestamp') or '')[:10]} | {clean}")
    return "\n".join(sorted(rows, reverse=True))


_portal: dict = {}


def _portal_info() -> tuple[str, str]:
    if not _portal:
        info = _call("GET", "/account-info/v3/details")
        _portal["id"] = str(info.get("portalId", ""))
        _portal["ui"] = info.get("uiDomain") or "app.hubspot.com"
    return _portal["id"], _portal["ui"]


def im_crm_oeffnen(kontakt_id: str = "", deal_id: str = "") -> str:
    """Oeffnet einen Kontakt oder Deal in HubSpot im Browser (nur Ansicht, aendert nichts)."""
    import webbrowser

    if not (kontakt_id or deal_id):
        raise ToolFailure("kontakt_id oder deal_id angeben.")
    portal, ui = _portal_info()
    if not portal:
        raise ToolFailure("HubSpot-Portal-ID nicht ermittelbar.")
    opened = []
    if kontakt_id:
        url = f"https://{ui}/contacts/{portal}/record/0-1/{kontakt_id}"
        webbrowser.open(url)
        opened.append(url)
    if deal_id:
        url = f"https://{ui}/contacts/{portal}/record/0-3/{deal_id}"
        webbrowser.open(url)
        opened.append(url)
    return "Im Browser geoeffnet: " + ", ".join(opened)


# --- Schreiben -----------------------------------------------------------------

def _create_with_associations(obj_type: str, props: dict, kontakt_id: str | None, deal_id: str | None) -> str:
    created = _call("POST", f"/crm/v3/objects/{obj_type}", json={"properties": props})
    new_id = created["id"]
    problems = []
    for to_type, to_id in (("contacts", kontakt_id), ("deals", deal_id)):
        if to_id:
            try:
                _call("PUT", f"/crm/v4/objects/{obj_type}/{new_id}/associations/default/{to_type}/{to_id}")
            except ToolFailure as exc:
                problems.append(f"Verknuepfung mit {to_type} {to_id} fehlgeschlagen: {exc}")
    if problems:
        raise ToolFailure(f"{obj_type} id={new_id} wurde angelegt, aber: " + "; ".join(problems))
    return new_id


def notiz_erstellen(text: str, bestaetigt_vom_nutzer: bool, kontakt_id: str = "", deal_id: str = "") -> str:
    _need_confirm(bestaetigt_vom_nutzer, "Notiz")
    if not (kontakt_id or deal_id):
        raise ToolFailure("Gib mindestens eine kontakt_id oder deal_id an, damit die Notiz irgendwo haengt.")
    new_id = _create_with_associations("notes", {"hs_note_body": text, "hs_timestamp": _now_iso()},
                                       kontakt_id or None, deal_id or None)
    return f"Notiz in HubSpot gespeichert (id={new_id})."


def aufgabe_erstellen(titel: str, bestaetigt_vom_nutzer: bool, faellig: str = "", beschreibung: str = "",
                      prioritaet: str = "MEDIUM", kontakt_id: str = "", deal_id: str = "") -> str:
    _need_confirm(bestaetigt_vom_nutzer, "Aufgabe")
    prio = prioritaet.upper()
    if prio not in ("LOW", "MEDIUM", "HIGH"):
        raise ToolFailure("prioritaet muss LOW, MEDIUM oder HIGH sein")
    props = {"hs_task_subject": titel, "hs_task_body": beschreibung, "hs_task_status": "NOT_STARTED",
             "hs_task_priority": prio, "hs_timestamp": _due_iso(faellig)}
    new_id = _create_with_associations("tasks", props, kontakt_id or None, deal_id or None)
    return f"HubSpot-Aufgabe erstellt: {titel} (id={new_id}, faellig {props['hs_timestamp'][:10]})."


def mail_protokollieren(betreff: str, text: str, bestaetigt_vom_nutzer: bool, kontakt_id: str = "",
                        deal_id: str = "", richtung: str = "ausgehend") -> str:
    """Legt eine E-Mail als Aktivitaet im HubSpot-Verlauf ab (versendet nichts)."""
    _need_confirm(bestaetigt_vom_nutzer, "Protokollieren")
    if not (kontakt_id or deal_id):
        raise ToolFailure("Gib mindestens eine kontakt_id oder deal_id an.")
    direction = "INCOMING_EMAIL" if richtung.lower().startswith(("ein", "in")) else "EMAIL"
    props = {"hs_timestamp": _now_iso(), "hs_email_direction": direction,
             "hs_email_subject": betreff, "hs_email_text": text, "hs_email_status": "SENT"}
    new_id = _create_with_associations("emails", props, kontakt_id or None, deal_id or None)
    return f"E-Mail im HubSpot-Verlauf protokolliert (id={new_id})."


def mail_senden_und_protokollieren(an: str, betreff: str, text: str, bestaetigt_vom_nutzer: bool,
                                   kontakt_id: str = "", deal_id: str = "") -> str:
    """Versendet die Mail ueber Gmail (HubSpot hat dafuer keine freie API) und protokolliert sie danach."""
    _need_confirm(bestaetigt_vom_nutzer, "Versand")
    from . import google

    if not google.is_connected():
        raise ToolFailure("Gmail ist nicht verbunden. Einmalig: python -m jarvis --google-verbinden datei.json")
    sent = google.envoyer_mail(an, betreff, text, True)
    if not (kontakt_id or deal_id):
        return sent + " In HubSpot wurde nichts protokolliert (keine kontakt_id/deal_id angegeben)."
    try:
        logged = mail_protokollieren(betreff, text, True, kontakt_id, deal_id)
    except ToolFailure as exc:
        return f"{sent} ACHTUNG: Protokollieren in HubSpot fehlgeschlagen: {exc}"
    return f"{sent} {logged}"


def tools() -> list[Tool]:
    if not is_configured():
        return []
    confirm = boolean("true NUR wenn der Nutzer gerade in seiner letzten Nachricht ausdruecklich 'ja' zu GENAU dieser Aktion gesagt hat")
    kid = string("HubSpot-Kontakt-ID (aus hubspot_kontakt_suchen)")
    did = string("HubSpot-Deal-ID (aus hubspot_deals_suchen)")
    return [
        Tool("hubspot_kontakt_suchen",
             "Sucht Kontakte im HubSpot-CRM nach Name, E-Mail, Telefon oder Firma. Liefert id, Name, Firma, Mail, Telefon, Phase.",
             {"suche": string("Name, Mail oder Firma"), "anzahl": {"type": "integer", "description": "max. Treffer (Standard 5)"}},
             kontakt_suchen, ["suche"]),
        Tool("hubspot_deals_suchen",
             "Sucht Deals in HubSpot (Name, Betrag, Phase, Abschlussdatum). Ohne Suchbegriff: alle offenen Deals, nach Abschlussdatum sortiert.",
             {"suche": string("Dealname oder Firma (optional)"),
              "nur_offene": boolean("nur offene Deals (Standard true)"),
              "anzahl": {"type": "integer", "description": "max. Treffer (Standard 10)"}},
             deals_suchen),
        Tool("hubspot_deals_zu_kontakt", "Zeigt alle Deals, die mit einem Kontakt verknuepft sind.",
             {"kontakt_id": kid}, deals_zu_kontakt, ["kontakt_id"]),
        Tool("hubspot_im_crm_oeffnen", "Oeffnet einen Kontakt und/oder Deal direkt in HubSpot im Browser des Nutzers "
             "(nur Ansicht, aendert nichts, keine Freigabe noetig).",
             {"kontakt_id": kid, "deal_id": did}, im_crm_oeffnen),
        Tool("hubspot_notizen_lesen", "Liest die letzten Notizen zu einem Kontakt (Gespraechsverlauf).",
             {"kontakt_id": kid, "anzahl": {"type": "integer", "description": "Standard 5"}},
             notizen_lesen, ["kontakt_id"]),
        Tool("hubspot_notiz_erstellen",
             "Speichert eine Notiz an einem Kontakt und/oder Deal. Erst nach ausdruecklichem Ja des Nutzers.",
             {"text": string("Notiztext"), "bestaetigt_vom_nutzer": confirm, "kontakt_id": kid, "deal_id": did},
             notiz_erstellen, ["text", "bestaetigt_vom_nutzer"]),
        Tool("hubspot_aufgabe_erstellen",
             "Erstellt eine HubSpot-Aufgabe (Follow-up) und verknuepft sie mit Kontakt/Deal. Erst nach ausdruecklichem Ja.",
             {"titel": string("Titel der Aufgabe"), "bestaetigt_vom_nutzer": confirm,
              "faellig": string("AAAA-MM-TT oder AAAA-MM-TTTHH:MM, Standard morgen 9 Uhr"),
              "beschreibung": string("Details (optional)"),
              "prioritaet": string("LOW, MEDIUM oder HIGH"), "kontakt_id": kid, "deal_id": did},
             aufgabe_erstellen, ["titel", "bestaetigt_vom_nutzer"]),
        Tool("hubspot_mail_protokollieren",
             "Legt eine bereits gesendete/empfangene E-Mail als Aktivitaet im HubSpot-Verlauf ab. Versendet nichts.",
             {"betreff": string("Betreff"), "text": string("Mailtext"), "bestaetigt_vom_nutzer": confirm,
              "kontakt_id": kid, "deal_id": did, "richtung": string("'ausgehend' (Standard) oder 'eingehend'")},
             mail_protokollieren, ["betreff", "text", "bestaetigt_vom_nutzer"]),
        Tool("hubspot_mail_senden",
             "Sendet eine E-Mail ueber Gmail an einen Kontakt und protokolliert sie danach in HubSpot. "
             "Lies dem Nutzer Empfaenger, Betreff und Text vor und warte auf sein ausdrueckliches Ja.",
             {"an": string("E-Mail-Adresse des Empfaengers"), "betreff": string("Betreff"), "text": string("Mailtext"),
              "bestaetigt_vom_nutzer": confirm, "kontakt_id": kid, "deal_id": did},
             mail_senden_und_protokollieren, ["an", "betreff", "text", "bestaetigt_vom_nutzer"]),
    ]
