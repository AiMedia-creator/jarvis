#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
if command -v python3.12 >/dev/null 2>&1; then PYTHON=python3.12; else PYTHON=python3; fi
command -v "$PYTHON" >/dev/null 2>&1 || { echo 'Python 3.12 installieren und erneut starten.' >&2; exit 1; }
"$PYTHON" -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ erforderlich, 3.12 empfohlen"'
if [ -x "$ROOT/.venv/bin/python" ]; then
  VENV="$ROOT/.venv"
else
  VENV="${JARVIS_VENV:-$HOME/.jarvis/venvs/v3}"
fi
if [ ! -x "$VENV/bin/python" ]; then "$PYTHON" -m venv "$VENV"; fi
if ! "$VENV/bin/python" -c 'import aiohttp, anthropic, requests, defusedxml, googleapiclient, google_auth_oauthlib, edge_tts, telethon; from anthropic.lib.tools._beta_builtin_memory_tool import BetaLocalFilesystemMemoryTool' >/dev/null 2>&1; then
  "$VENV/bin/python" -m pip install -r "$ROOT/requirements-web.txt"
fi
LIVE_MODE=0
for ARG in "$@"; do if [ "$ARG" = '--live' ]; then LIVE_MODE=1; fi; done
if [ "$LIVE_MODE" = '1' ]; then
  if ! "$VENV/bin/python" -c 'import faster_whisper, numpy' >/dev/null 2>&1; then
    "$VENV/bin/python" -m pip install 'numpy>=1.26,<3' 'faster-whisper>=1.2,<2'
  fi
fi
if [ -x /usr/libexec/java_home ]; then
  JAVA_HOME="$(/usr/libexec/java_home -v 21 2>/dev/null || true)"
  if [ -n "$JAVA_HOME" ]; then export JAVA_HOME; export PATH="$JAVA_HOME/bin:$PATH"; fi
fi
command -v java >/dev/null 2>&1 || { echo 'Java 21 erforderlich; auf dem Mac z. B. brew install openjdk@21.' >&2; exit 1; }
JAVA_VERSION="$(java -XshowSettings:properties -version 2>&1 | awk '/java.specification.version =/{print $3; exit}')"
case "$JAVA_VERSION" in ''|*[!0-9]*) echo 'Java 21+ erforderlich.' >&2; exit 1;; esac
[ "$JAVA_VERSION" -ge 21 ] || { echo 'Java 21+ erforderlich.' >&2; exit 1; }
if [ ! -f "$ROOT/java-backend/target/jarvis-java-backend-1.0.0.jar" ]; then
  (cd "$ROOT/java-backend" && ./mvnw test package)
fi
if [ ! -f "$ROOT/dashboard/dist/index.html" ]; then
  command -v pnpm >/dev/null 2>&1 || { echo 'Für den Frontend-Neubau Node.js 22 und pnpm installieren.' >&2; exit 1; }
  (cd "$ROOT/dashboard" && pnpm install --frozen-lockfile && pnpm build)
fi
exec "$VENV/bin/python" "$ROOT/scripts/launch_v3.py" "$@"
