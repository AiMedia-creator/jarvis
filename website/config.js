// Zentrale Konfiguration der Taktwerk-Website.
// Die Werte hier gelten für alle Seiten. Die HTML-Dateien tragen dieselben
// Werte als Standard, damit die Seite auch ohne JavaScript funktioniert.
// Beim Ändern: hier anpassen UND die href="…" der Links mit data-booking in
// index.html, impressum.html und datenschutz.html nachziehen (Suchen und Ersetzen).
window.TAKTWERK = {
  agency: "Taktwerk",
  // Link zum Strategiegespräch mit Taktwerk (nicht der Kundentermin).
  bookingUrl: "https://calendar.app.google/xHqQqeMYB5hZDwi79",
  // Bestätigte CRM-Systeme, nur hier und im HTML nennen, wenn sie bestätigt sind.
  crm: ["HubSpot", "Close"],
};

(function () {
  var cfg = window.TAKTWERK;
  document.querySelectorAll("[data-booking]").forEach(function (a) {
    a.setAttribute("href", cfg.bookingUrl);
    a.setAttribute("target", "_blank");
    a.setAttribute("rel", "noopener");
  });
})();
