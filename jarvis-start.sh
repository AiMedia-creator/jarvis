#!/bin/bash
# Startet Jarvis mit den lokal installierten Java- und Python-Versionen.
# Aufruf: ./jarvis-start.sh              (echter Modus)
#         ./jarvis-start.sh demo         (Demomodus, ohne echte KI-Kosten)
#         ./jarvis-start.sh hintergrund  (echter Modus ohne Browserfenster, z. B. für den Autostart)
#         ./jarvis-start.sh neustart     (laufenden Jarvis beenden und neu starten, z. B. nach einem Update)
# Läuft Jarvis schon (z. B. durch den Autostart), wird nur die Oberfläche geöffnet statt eines zweiten Starts.
cd "$(dirname "$0")" || exit 1
export JAVA_HOME="$HOME/.local/tools/jdk-21.0.12.1+1/Contents/Home"
export PATH="$JAVA_HOME/bin:$HOME/.local/share/uv/python/cpython-3.12.15-macos-aarch64-none/bin:$PATH"
if [ "${1:-}" = "neustart" ]; then
  # Laufenden Jarvis sauber beenden (auch wenn er im Hintergrund per Autostart läuft) und neu starten.
  # Über alle drei Ports suchen: auch ein halb laufender Jarvis (nur Oberfläche oder nur Java) wird gefunden.
  BRIDGE=$(lsof -t -nP -iTCP:8766 -iTCP:8765 -iTCP:8080 -sTCP:LISTEN 2>/dev/null | head -1)
  if [ -n "$BRIDGE" ]; then
    LAUNCHER=$(ps -o ppid= -p "$BRIDGE" | tr -d ' ')
    if ps -o command= -p "$LAUNCHER" | grep -q "launch_v3.py"; then
      echo "Beende laufenden Jarvis …"
      kill -INT "$LAUNCHER"
      for _ in $(seq 1 30); do lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null | grep -qE ':(8080|8765|8766) ' || break; sleep 0.5; done
    fi
  fi
  exec ./start-jarvis-v3.sh --live
fi
UI_UP=0; BRIDGE_UP=0
curl -fs -m 2 http://127.0.0.1:8080/health >/dev/null 2>&1 && UI_UP=1
curl -fs -m 2 http://127.0.0.1:8766/health >/dev/null 2>&1 && BRIDGE_UP=1
if [ "$UI_UP" != "$BRIDGE_UP" ] || { [ "$UI_UP" = 0 ] && lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null | grep -qE ':(8080|8765|8766) '; }; then
  # Halb laufender Jarvis (z. B. Oberfläche da, Agenten-Brücke weg): sauber neu starten statt Port-Konflikt.
  echo "Jarvis läuft nur teilweise – starte neu …"
  exec "$0" neustart
fi
if [ "$UI_UP" = 1 ] && [ "$BRIDGE_UP" = 1 ]; then
  if [ "${1:-}" != "hintergrund" ]; then
    echo "Jarvis läuft bereits – öffne das Dashboard: http://localhost:8080"
    open "http://localhost:8080"
  fi
  exit 0
fi
if [ "${1:-}" = "demo" ]; then
  exec ./start-jarvis-v3.sh --demo
fi
if [ "${1:-}" = "hintergrund" ]; then
  exec ./start-jarvis-v3.sh --live --no-browser
fi
exec ./start-jarvis-v3.sh --live
