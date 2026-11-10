"""Jarvis öffnen/fokussieren – die EINZIGE Wirkung eines erkannten Klatschens/Schnipsens.

- Bildschirm gesperrt: nichts anzeigen.
- Jarvis läuft und ein Browser-Tab mit Jarvis ist offen: diesen Tab nach vorn holen (Safari/Chrome per AppleScript).
- Jarvis läuft, kein Tab: die bekannte lokale Adresse einmal öffnen (höchstens einmal pro 60 s, keine Popup-Flut).
- Jarvis läuft nicht: fester Start über jarvis-start.sh (nie aus Klang/Modell abgeleitete Befehle), Singleton-Sperre,
  Bereitschaft abwarten, dann öffnen. Kein zweiter paralleler Jarvis.
"""

from __future__ import annotations

import fcntl
import os
import subprocess
import time
import urllib.request
from pathlib import Path

from .. import config

ROOT = Path(__file__).resolve().parents[2]
OPEN_GUARD_S = 60.0
_last_open = 0.0


def screen_locked() -> bool:
    try:
        out = subprocess.run(["/usr/sbin/ioreg", "-n", "Root", "-d1", "-a"], capture_output=True, text=True, timeout=3).stdout
    except Exception:
        return False
    marker = out.find("<key>IOConsoleLocked</key>")
    return marker >= 0 and "<true/>" in out[marker:marker + 60]


def healthy(timeout: float = 1.5) -> bool:
    for url in ("http://127.0.0.1:8080/health", "http://127.0.0.1:8766/health"):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                if r.status != 200:
                    return False
        except Exception:
            return False
    return True


_FOCUS = '''
on run argv
  set target to item 1 of argv
  repeat with appName in {"Google Chrome", "Safari", "Arc", "Brave Browser", "Microsoft Edge"}
    if application appName is running then
      try
        if appName as text is "Safari" then
          tell application "Safari"
            repeat with w in windows
              repeat with t in tabs of w
                if URL of t starts with target then
                  set current tab of w to t
                  set index of w to 1
                  activate
                  return "fokussiert"
                end if
              end repeat
            end repeat
          end tell
        else
          tell application appName
            repeat with w in windows
              set i to 0
              repeat with t in tabs of w
                set i to i + 1
                if URL of t starts with target then
                  set active tab index of w to i
                  set index of w to 1
                  activate
                  return "fokussiert"
                end if
              end repeat
            end repeat
          end tell
        end if
      end try
    end if
  end repeat
  return "kein_tab"
end run
'''


def focus_tab(url: str) -> bool:
    try:
        out = subprocess.run(["osascript", "-e", _FOCUS, url], capture_output=True, text=True, timeout=5)
        return out.stdout.strip() == "fokussiert"
    except Exception:
        return False


def open_url(url: str) -> bool:
    global _last_open
    if time.time() - _last_open < OPEN_GUARD_S:
        return False
    _last_open = time.time()
    subprocess.run(["open", url], capture_output=True, timeout=5)
    return True


def start_runtime(wait_s: float = 60.0) -> bool:
    """Startet Jarvis über den festen Starter, mit Singleton-Sperre; True, wenn er danach bereit ist."""
    lock_path = config.data_dir() / "aktivierung.lock"
    with open(lock_path, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False  # ein anderer Start läuft schon
        if healthy():
            return True
        log = config.data_dir() / "logs"
        log.mkdir(parents=True, exist_ok=True)
        with open(log / "jarvis.out.log", "ab") as out, open(log / "jarvis.err.log", "ab") as err:
            subprocess.Popen([str(ROOT / "jarvis-start.sh"), "hintergrund"], stdout=out, stderr=err,
                             stdin=subprocess.DEVNULL, start_new_session=True, cwd=str(ROOT), env=os.environ.copy())
        end = time.time() + wait_s
        while time.time() < end:
            if healthy():
                return True
            time.sleep(1.0)
        return False


def bring_up(url: str) -> str:
    """Öffnen/fokussieren nach Richtlinie; Rückgabe ist ein ehrlicher Ergebnis-Code."""
    if screen_locked():
        return "gesperrt"
    if not healthy():
        if not start_runtime():
            return "start_fehlgeschlagen"
        return "gestartet_geoeffnet" if open_url(url) else "gestartet"
    if focus_tab(url):
        return "fokussiert"
    return "geoeffnet" if open_url(url) else "schon_geoeffnet"
