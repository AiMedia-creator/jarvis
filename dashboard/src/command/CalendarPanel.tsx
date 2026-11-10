import FullCalendar from "@fullcalendar/react";
import dayGridPlugin from "@fullcalendar/daygrid";
import timeGridPlugin from "@fullcalendar/timegrid";
import deLocale from "@fullcalendar/core/locales/de";
import type { DatesSetArg, EventClickArg, EventInput } from "@fullcalendar/core";
import type { CalendarData, CalendarEvent } from "./api";

/** Monat/Woche/Tag mit FullCalendar (Standard-Lizenz). Daten kommen strukturiert aus /api/calendar. */
export default function CalendarPanel({
  data,
  onRange,
  onSelect,
}: {
  data: CalendarData | null;
  onRange: (von: string, bis: string) => void;
  onSelect: (event: CalendarEvent) => void;
}) {
  const conflicts = new Set((data?.konflikte ?? []).flatMap((k) => [k.a, k.b]));
  const events: EventInput[] = (data?.termine ?? [])
    .filter((e) => e.status !== "cancelled" && e.gueltig)
    .map((e) =>
      e.ganztaegig
        ? { id: e.id, title: e.titel, start: e.datum_beginn!, end: e.datum_ende ?? undefined, allDay: true,
            classNames: ["cc-fc-allday"], extendedProps: { source: e } }
        : { id: e.id, title: e.titel, start: e.beginn!, end: e.ende!, allDay: false,
            classNames: [conflicts.has(e.id) ? "cc-fc-conflict" : "cc-fc-event", e.frei ? "cc-fc-free" : ""],
            extendedProps: { source: e } },
    );
  const local = (d: Date) => {
    const pad = (n: number) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T00:00:00`;
  };
  return (
    <div className="cc-calendar">
      {data && data.status !== "ok" && <div className="cc-error" role="status">{data.hinweis || "Kalender nicht verfügbar."}</div>}
      <FullCalendar
        plugins={[dayGridPlugin, timeGridPlugin]}
        locale={deLocale}
        initialView="timeGridWeek"
        headerToolbar={{ left: "prev,next today", center: "title", right: "dayGridMonth,timeGridWeek,timeGridDay" }}
        nowIndicator
        height="100%"
        firstDay={1}
        slotMinTime="06:00:00"
        slotMaxTime="22:00:00"
        weekNumbers
        events={events}
        datesSet={(arg: DatesSetArg) => onRange(local(arg.start), local(arg.end))}
        eventClick={(arg: EventClickArg) => onSelect(arg.event.extendedProps.source as CalendarEvent)}
      />
    </div>
  );
}
