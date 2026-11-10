"""Strukturierte Kalenderdaten (JSON) aus Google Kalender für Kommandozentrale, Agenda und Agent.

Je Termin: Event-ID, Kalender-ID/-Name, Titel, Beginn/Ende mit Zeitzonen-Offset ODER Ganztagsdatum, explizite
IANA-Zeitzone, Teilnehmer, Ort, Meetinglink, Serien-/Ausnahmebezug, Status und ETag. Paginierung über alle
freigegebenen (im Google-Kalender ausgewählten) Kalender, Serien werden als Einzeltermine geliefert
(singleEvents) und behalten ihren Serienbezug. Ende muss nach Beginn liegen; sonst wird der Termin als
'ungueltig' markiert statt still korrigiert. Konflikte = zeitliche Überschneidung zweier zugesagter Termine.

Nur lesend. Termine ändern/löschen/synchronisieren gehört nicht hierher (Anlegen bleibt termin_erstellen mit Freigabe).
"""

from __future__ import annotations

import datetime
import re
from zoneinfo import ZoneInfo

MAX_EVENTS = 500
MAX_PAGES = 10
_MEET = re.compile(r"https://(?:meet\.google\.com|[\w.-]*zoom\.us|teams\.microsoft\.com)/\S+")


def _local_tz() -> str:
    try:
        key = getattr(datetime.datetime.now().astimezone().tzinfo, "key", None)
        if key:
            return key
        import os

        link = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in link:
            return link.split("zoneinfo/", 1)[1]
    except Exception:
        pass
    return "Europe/Berlin"


def _parse_range(von: str, bis: str, tz: str) -> tuple[datetime.datetime, datetime.datetime]:
    zone = ZoneInfo(tz)
    today = datetime.datetime.now(zone).replace(hour=0, minute=0, second=0, microsecond=0)
    start = datetime.datetime.fromisoformat(von).replace(tzinfo=zone) if von else today
    end = datetime.datetime.fromisoformat(bis).replace(tzinfo=zone) if bis else start + datetime.timedelta(days=7)
    if end <= start:
        raise ValueError("'bis' muss nach 'von' liegen.")
    if end - start > datetime.timedelta(days=93):
        raise ValueError("Höchstens 93 Tage auf einmal.")
    return start, end


def normalize(ev: dict, calendar: dict, default_tz: str) -> dict:
    start, end = ev.get("start", {}), ev.get("end", {})
    all_day = "date" in start and "dateTime" not in start
    tz = start.get("timeZone") or calendar.get("timeZone") or default_tz
    out = {
        "id": ev.get("id"), "kalender_id": calendar.get("id"), "kalender": calendar.get("summary", ""),
        "titel": ev.get("summary") or "(ohne Titel)", "ganztaegig": all_day, "zeitzone": tz,
        "beginn": None, "ende": None, "datum_beginn": None, "datum_ende": None,
        "teilnehmer": [{"email": a.get("email", ""), "name": a.get("displayName", ""),
                        "antwort": a.get("responseStatus", ""), "ich": bool(a.get("self"))}
                       for a in (ev.get("attendees") or [])[:50]],
        "ort": ev.get("location", ""), "meeting_link": "", "serie_id": ev.get("recurringEventId"),
        "ausnahme": bool(ev.get("recurringEventId") and ev.get("originalStartTime")
                         and ev.get("originalStartTime") != ev.get("start")),
        "status": ev.get("status", "confirmed"), "frei": ev.get("transparency") == "transparent",
        "etag": ev.get("etag"), "link": ev.get("htmlLink", ""), "gueltig": True,
    }
    entry = next((e.get("uri") for e in ((ev.get("conferenceData") or {}).get("entryPoints") or [])
                  if e.get("entryPointType") == "video"), None)
    found = _MEET.search(" ".join(str(ev.get(k, "")) for k in ("location", "description")))
    out["meeting_link"] = ev.get("hangoutLink") or entry or (found.group(0) if found else "")
    if all_day:
        out["datum_beginn"], out["datum_ende"] = start.get("date"), end.get("date")  # Ende exklusiv (Google)
        out["gueltig"] = bool(out["datum_ende"] and out["datum_ende"] > out["datum_beginn"])
    else:
        try:
            b = datetime.datetime.fromisoformat(start["dateTime"])
            e = datetime.datetime.fromisoformat(end["dateTime"])
            out["beginn"], out["ende"] = b.isoformat(), e.isoformat()
            out["gueltig"] = e > b
        except (KeyError, ValueError):
            out["gueltig"] = False
    return out


def conflicts(events: list[dict]) -> list[dict]:
    """Überschneidungen zugesagter, belegender Termine (ohne Ganztägiges, Abgesagtes, 'frei')."""
    timed = sorted((e for e in events if not e["ganztaegig"] and e["gueltig"] and e["status"] != "cancelled"
                    and not e["frei"] and not any(t["ich"] and t["antwort"] == "declined" for t in e["teilnehmer"])),
                   key=lambda e: e["beginn"])
    out = []
    for i, a in enumerate(timed):
        a_end = datetime.datetime.fromisoformat(a["ende"])
        for b in timed[i + 1:]:
            b_start = datetime.datetime.fromisoformat(b["beginn"])
            if b_start >= a_end:
                break
            out.append({"a": a["id"], "b": b["id"], "titel": [a["titel"], b["titel"]],
                        "ab": max(a["beginn"], b["beginn"]), "bis": min(a["ende"], b["ende"])})
    return out[:100]


def events(von: str = "", bis: str = "", service=None) -> dict:
    from .tools import google

    if service is None and not google.is_connected():
        return {"status": "nicht_verbunden", "hinweis": "Google ist nicht verbunden.", "termine": [], "konflikte": []}
    tz = _local_tz()
    start, end = _parse_range(von, bis, tz)
    cal = service or google._service("calendar", "v3")
    calendars, token = [], None
    for _ in range(MAX_PAGES):
        page = cal.calendarList().list(pageToken=token, minAccessRole="reader").execute()
        calendars += [c for c in page.get("items", []) if c.get("selected") or c.get("primary")]
        token = page.get("nextPageToken")
        if not token:
            break
    result, truncated = [], False
    for c in calendars:
        token = None
        for _ in range(MAX_PAGES):
            page = cal.events().list(calendarId=c["id"], timeMin=start.isoformat(), timeMax=end.isoformat(),
                                     singleEvents=True, orderBy="startTime", maxResults=250, pageToken=token,
                                     timeZone=tz, showDeleted=False).execute()
            result += [normalize(ev, c, tz) for ev in page.get("items", [])]
            token = page.get("nextPageToken")
            if not token or len(result) >= MAX_EVENTS:
                truncated = truncated or bool(token)
                break
    result = result[:MAX_EVENTS]
    result.sort(key=lambda e: e["beginn"] or (e["datum_beginn"] + "T00:00:00") if (e["beginn"] or e["datum_beginn"]) else "")
    return {"status": "ok", "zeitzone": tz, "von": start.isoformat(), "bis": end.isoformat(),
            "kalender": [{"id": c["id"], "name": c.get("summary", ""), "farbe": c.get("backgroundColor", "")}
                         for c in calendars],
            "termine": result, "konflikte": conflicts(result), "gekuerzt": truncated}


def as_text(data: dict) -> str:
    """Kompakte Textform für den Agenten (mit IDs, Zeitzone und Konflikten)."""
    if data.get("status") != "ok":
        return data.get("hinweis", "Kalender nicht verfügbar.")
    lines = []
    for e in data["termine"]:
        when = (f"{e['datum_beginn']} ganztägig" if e["ganztaegig"] else f"{e['beginn']} -> {e['ende']}")
        extra = "".join([f" | Ort: {e['ort']}" if e["ort"] else "", f" | Link: {e['meeting_link']}" if e["meeting_link"] else "",
                         f" | Teilnehmer: {len(e['teilnehmer'])}" if e["teilnehmer"] else "",
                         " | Serie" if e["serie_id"] else "", " | UNGÜLTIG (Ende vor Beginn)" if not e["gueltig"] else "",
                         " | abgesagt" if e["status"] == "cancelled" else ""])
        lines.append(f"[{e['id']}] {when} | {e['titel']} ({e['kalender']}){extra}")
    for k in data["konflikte"]:
        lines.append(f"KONFLIKT: '{k['titel'][0]}' überschneidet sich mit '{k['titel'][1]}' ({k['ab']} bis {k['bis']})")
    head = f"Zeitzone {data['zeitzone']}, {len(data['termine'])} Termine" + (" (gekürzt)" if data.get("gekuerzt") else "")
    return head + ("\n" + "\n".join(lines) if lines else "\nKeine Termine in diesem Zeitraum.")
