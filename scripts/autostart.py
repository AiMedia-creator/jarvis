#!/usr/bin/env python3
"""Jarvis beim Anmelden am Mac automatisch starten. Ausdrückliches Opt-in.

    python3 scripts/autostart.py status       # zeigt, ob der Autostart eingerichtet ist
    python3 scripts/autostart.py install      # legt die Start-App an und trägt sie bei "Bei der Anmeldung öffnen" ein
    python3 scripts/autostart.py uninstall    # entfernt Eintrag und Start-App wieder (Jarvis läuft bis zum Abmelden weiter)

Warum eine Start-App statt eines launchd-Dienstes: macOS schützt den Schreibtisch-Ordner. Ein Hintergrunddienst darf
dort ohne ausdrückliche Erlaubnis nichts öffnen ("Operation not permitted"). Die kleine App "Jarvis Autostart"
(~/Applications) fragt beim ersten Start einmal nach dem Zugriff auf den Schreibtisch; danach startet sie Jarvis bei
jeder Anmeldung im Echtmodus ohne Browserfenster und beendet sich selbst. Läuft Jarvis schon, tut sie nichts.

Grenzen: Stürzt Jarvis ab, startet ihn die App nicht neu (dann im Terminal `jarvis`). Ein ausgeschalteter oder
schlafender Mac führt nichts aus. Dienste bleiben auf 127.0.0.1, das Mikrofon nutzt nur die geöffnete Oberfläche.
Logs: ~/.jarvis/logs/.
"""

from __future__ import annotations

import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

NAME = "Jarvis Autostart"
ROOT = Path(__file__).resolve().parent.parent
APP = Path.home() / "Applications" / f"{NAME}.app"
LOGS = Path.home() / ".jarvis" / "logs"
OLD_PLIST = Path.home() / "Library" / "LaunchAgents" / "de.jarvis.assistent.plist"


def _osa(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(["osascript", "-e", script], capture_output=True, text=True)


def applescript() -> str:
    start = ROOT / "jarvis-start.sh"
    python = Path.home() / ".jarvis" / "venvs" / "v3" / "bin" / "python"
    # Klatsch-/Schnips-Bereitschaft: startet nur, wenn sie per Setup eingeschaltet wurde (sonst sofort beendet).
    shell = (f"source ~/.zshrc >/dev/null 2>&1; mkdir -p '{LOGS}'; "
             f"nohup '{start}' hintergrund >> '{LOGS}/jarvis.out.log' 2>> '{LOGS}/jarvis.err.log' < /dev/null & "
             f"cd '{ROOT}' && nohup '{python}' -m jarvis.activation start >> '{LOGS}/aktivierung.log' 2>&1 < /dev/null &")
    escaped = shell.replace("\\", "\\\\").replace('"', '\\"')
    return f'do shell script "/bin/zsh -c " & quoted form of "{escaped}"'


def login_item_present() -> bool:
    out = _osa('tell application "System Events" to get the name of every login item')
    return out.returncode == 0 and NAME in out.stdout


def status() -> int:
    present = login_item_present()
    print(f"Start-App: {'vorhanden' if APP.exists() else 'fehlt'} ({APP})")
    print(f"Bei der Anmeldung öffnen: {'eingetragen' if present else 'nicht eingetragen'}")
    return 0 if APP.exists() and present else 1


def build() -> None:
    APP.parent.mkdir(parents=True, exist_ok=True)
    if APP.exists():
        shutil.rmtree(APP)
    with tempfile.NamedTemporaryFile("w", suffix=".applescript", delete=False) as fh:
        fh.write(applescript() + "\n")
        source = fh.name
    subprocess.run(["osacompile", "-o", str(APP), source], check=True, capture_output=True)
    Path(source).unlink(missing_ok=True)
    info = APP / "Contents" / "Info.plist"
    data = plistlib.loads(info.read_bytes())
    data.update({"CFBundleIdentifier": "de.jarvis.autostart", "LSUIElement": True,  # kein Dock-Symbol
                 "NSDesktopFolderUsageDescription": "Jarvis liegt auf deinem Schreibtisch und wird von dort gestartet.",
                 "NSMicrophoneUsageDescription": "Nur wenn du die Klatsch-/Schnips-Bereitschaft eingeschaltet hast: "
                                                 "lokal auf Klatschen oder Schnipsen hören, um Jarvis zu öffnen. "
                                                 "Nichts wird gespeichert oder hochgeladen.",
                 "NSAppleEventsUsageDescription": "Ein vorhandenes Jarvis-Fenster im Browser nach vorn holen."})
    info.write_bytes(plistlib.dumps(data))
    # Info.plist geändert -> Signatur erneuern (lokal, ad hoc), sonst startet macOS die App nicht.
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(APP)], check=True, capture_output=True)


def install() -> int:
    if sys.platform != "darwin":
        print("Autostart gibt es nur auf dem Mac (Windows/Linux: nicht unterstützt).")
        return 2
    if OLD_PLIST.exists():  # früherer launchd-Versuch: sauber entfernen
        subprocess.run(["launchctl", "bootout", f"gui/{__import__('os').getuid()}/de.jarvis.assistent"], capture_output=True)
        OLD_PLIST.unlink()
    LOGS.mkdir(parents=True, exist_ok=True)
    build()
    if not login_item_present():
        done = _osa(f'tell application "System Events" to make login item at end with properties '
                    f'{{path:"{APP}", hidden:true, name:"{NAME}"}}')
        if done.returncode != 0:
            print(f"Start-App angelegt, aber der Anmelde-Eintrag ging nicht: {done.stderr.strip()}")
            return 1
    print("Autostart eingerichtet: Start-App angelegt und bei der Anmeldung eingetragen.")
    return 0


def uninstall() -> int:
    _osa(f'tell application "System Events" to delete (every login item whose name is "{NAME}")')
    if APP.exists():
        shutil.rmtree(APP)
    if OLD_PLIST.exists():
        OLD_PLIST.unlink()
    print("Autostart entfernt.")
    return 0


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    sys.exit({"status": status, "install": install, "uninstall": uninstall}.get(action, status)())
