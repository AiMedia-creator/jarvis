#!/usr/bin/env python3
"""Gemeinsamer lokaler V3-Start; Prozessgruppen funktionieren unter macOS und Linux."""
from __future__ import annotations

import argparse
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--live', action='store_true', help='Vorhandene echte KI-/Sprachkonfiguration verwenden')
    group.add_argument('--demo', action='store_true', help='Expliziter Offline-Demomodus (Standard)')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--no-native', action='store_true', help='Nur React-Dashboard und Agenten-Brücke')
    args = parser.parse_args()
    environment = os.environ.copy()
    environment['JARVIS_DEMO'] = 'false' if args.live else 'true'
    environment['JARVIS_NO_BROWSER'] = '1'
    environment['JARVIS_NATIVE_UI'] = 'false' if args.no_native else 'true'
    environment['JARVIS_GENERATION_BACKEND'] = 'python'
    environment['JARVIS_BRIDGE_TOKEN'] = secrets.token_urlsafe(40)
    bridge_port = int(environment.get('JARVIS_BRIDGE_PORT', '8766'))
    ui_port = int(environment.get('JARVIS_UI_PORT', '8765'))
    port = int(environment.get('PORT', '8080'))
    environment['JARVIS_BRIDGE_URL'] = f'http://127.0.0.1:{bridge_port}'
    environment.setdefault('JARVIS_STT_URL', f'http://127.0.0.1:{bridge_port}/internal/transcribe')
    environment.setdefault('JARVIS_STT_API_KEY', environment['JARVIS_BRIDGE_TOKEN'])
    environment.setdefault('JARVIS_ALLOWED_ORIGINS', f'http://localhost:{port},http://127.0.0.1:{port},http://localhost:4173,http://127.0.0.1:4173')
    environment['JARVIS_STATIC_DIR'] = str(ROOT / 'dashboard' / 'dist')
    required_ports = [port, bridge_port] + ([] if args.no_native else [ui_port])
    if len(set(required_ports)) != len(required_ports):
        raise SystemExit('Die V3-Dienste benötigen unterschiedliche Ports.')
    for target in required_ports:
        with socket.socket() as sock:
            # Wie Netty/aiohttp: Verbindungen im TIME_WAIT zählen nicht als belegt.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(('127.0.0.1', target))
            except OSError:
                raise SystemExit(f'Port {target} ist bereits belegt. Bestehenden Dienst zuerst gezielt beenden.')
    jar = ROOT / 'java-backend' / 'target' / 'jarvis-java-backend-1.0.0.jar'
    if not jar.exists():
        raise SystemExit('Java-Build fehlt: cd java-backend && ./mvnw test package')
    children = []
    stopping = False
    def shutdown(signum=None, frame=None):
        nonlocal stopping
        if stopping:
            return
        stopping = True
        for process in reversed(children):
            if process.poll() is None:
                try: os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError: pass
        deadline = time.monotonic() + 8
        for process in reversed(children):
            try: process.wait(timeout=max(.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                try: os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    # Terminal-Fenster geschlossen ("Auflegen"): Dienste mit beenden, statt sie verwaist weiterlaufen zu lassen.
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, shutdown)
    def spawn(command, directory=ROOT):
        process = subprocess.Popen(command, cwd=directory, env=environment, start_new_session=True)
        children.append(process)
        return process
    def ready(url, process, timeout=45):
        end = time.monotonic() + timeout
        while time.monotonic() < end and not stopping:
            if process.poll() is not None:
                raise RuntimeError('Ein V3-Dienst konnte nicht starten. Siehe Fehlermeldung darüber.')
            try:
                with urllib.request.urlopen(url, timeout=2) as response:
                    if response.status == 200:
                        return
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(.25)
        raise RuntimeError('Zeitüberschreitung beim lokalen Dienststart.')
    try:
        print('Jarvis V3: ' + ('ECHTMODUS' if args.live else 'EXPLIZITER DEMOMODUS – echte SQLite-Persistenz, synthetische Antwort/Testton'), flush=True)
        bridge = spawn([sys.executable, '-m', 'jarvis.v3_bridge'])
        ready(f'http://127.0.0.1:{bridge_port}/health', bridge)
        java = spawn(['java', '-jar', str(jar)], ROOT / 'java-backend')
        ready(f'http://127.0.0.1:{port}/health', java)
        if not args.no_native:
            ready(f'http://127.0.0.1:{ui_port}/api/approvals', bridge)  # Startseite leitet nur noch auf 8080 um
        print(f'Command Center: http://localhost:{port} | Screen 2: OPEN OPS im Dashboard', flush=True)
        if not args.no_native:
            print(f'Alte Adresse http://localhost:{ui_port} leitet auf das Command Center um.', flush=True)
        print('Strg+C beendet alle gestarteten Dienste. Nicht mehrere Mikrofonmodi gleichzeitig aktivieren.', flush=True)
        if not args.no_browser:
            # Hauptoberfläche ist das Dashboard (8080); die Sprachoberfläche (8765) ist dort über die Navigation verlinkt.
            webbrowser.open(f'http://localhost:{port}')
        while not stopping:
            exited = [process for process in children if process.poll() is not None]
            if exited:
                raise RuntimeError('Ein V3-Dienst wurde beendet; die übrigen Dienste werden ebenfalls beendet.')
            time.sleep(.5)
    except (RuntimeError, KeyboardInterrupt) as exc:
        if not isinstance(exc, KeyboardInterrupt):
            print(str(exc), file=sys.stderr)
            return 1
    finally:
        shutdown()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
