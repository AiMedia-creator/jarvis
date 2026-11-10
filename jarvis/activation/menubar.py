"""Sichtbarer Status in der Menüleiste: Bereitschaft, Pause/Fortsetzen, Mikrofon freigeben, Jarvis öffnen, Beenden.

Solange der Dienst läuft, ist er hier immer sichtbar – auch wenn kein Jarvis-Fenster offen ist.
"""

from __future__ import annotations

import rumps

TITLES = {"BEREIT": "J ●", "PAUSIERT": "J ‖", "SPRACHE_AKTIV": "J ◐", "AUS": "J ○",
          "KEIN_GERAET": "J !", "KEIN_SIGNAL": "J !", "KEIN_MODELL": "J !"}
TEXT = {"BEREIT": "Bereit: Klatschen oder Schnipsen öffnet Jarvis",
        "PAUSIERT": "Pausiert – Mikrofon ist freigegeben",
        "SPRACHE_AKTIV": "Gespräch läuft – Mikrofon an Jarvis übergeben",
        "AUS": "Aus",
        "KEIN_GERAET": "Kein Mikrofon gefunden (wird erneut versucht)",
        "KEIN_SIGNAL": "Kein Signal – Mikrofon stumm oder keine Freigabe?",
        "KEIN_MODELL": "Erkennungsmodell fehlt – Setup ausführen"}


class ActivationApp(rumps.App):
    def __init__(self, service):
        super().__init__("Jarvis", title=TITLES.get(service.state, "J"), quit_button=None)
        self.service = service
        self.status_item = rumps.MenuItem(TEXT.get(service.state, service.state))
        self.toggle_item = rumps.MenuItem("Pausieren (Mikrofon freigeben)", callback=self.toggle)
        self.menu = [self.status_item, None, self.toggle_item, rumps.MenuItem("Jarvis öffnen", callback=self.open_jarvis),
                     None, rumps.MenuItem("Bereitschaft beenden", callback=self.quit_app)]
        self.timer = rumps.Timer(self.refresh, 1)
        self.timer.start()

    def refresh(self, _=None):
        state = self.service.state
        self.title = TITLES.get(state, "J")
        self.status_item.title = TEXT.get(state, state)
        self.toggle_item.title = "Fortsetzen" if self.service.paused else "Pausieren (Mikrofon freigeben)"

    def toggle(self, _):
        if self.service.paused:
            self.service.resume()
        else:
            self.service.pause()
        self.refresh()

    def open_jarvis(self, _):
        from .launcher import bring_up

        bring_up(self.service.settings.url)

    def quit_app(self, _):
        self.service.stop()
        rumps.quit_application()


def run(service) -> None:
    ActivationApp(service).run()
