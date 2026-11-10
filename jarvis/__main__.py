"""Start: python -m jarvis [--stimme] [--mikro | --freihaendig] ..."""

from __future__ import annotations

import argparse
import os
import re
import sys

from . import config


def check() -> int:
    """Prueft, was eingerichtet ist (zeigt nie die Schluessel selbst)."""
    from .tools import google, hubspot

    def ok(flag: bool) -> str:
        return "OK     " if flag else "FEHLT  "

    print(ok(bool(os.getenv("ANTHROPIC_API_KEY") or config.get("anthropic_key"))), "ANTHROPIC_API_KEY (Gehirn)")
    print(ok(bool(config.get("elevenlabs_key"))), "ELEVENLABS_API_KEY (schoene Stimme; sonst kostenlose Stimme)")
    print(ok(google.is_connected()), "Google (Gmail, Kalender, Drive, Aufgaben, Kontakte)")
    print(ok(hubspot.is_configured()), "HUBSPOT_TOKEN (CRM)")
    from .tools import telegram

    print(ok(telegram.is_configured()), "Telegram (Kanaele lesen)")
    from . import mcp_client, skills
    from .tools import extras, mac

    print(ok(bool(config.get("wetter_ort"))), "wetter_ort (Standardort fuer Wetter; optional)")
    print(ok(extras.github_configured()), "JARVIS_GITHUB_TOKEN (GitHub-Benachrichtigungen; optional)")
    print(ok(mac.NOTES_DB.exists()), "Apple Notizen (braucht Festplattenvollzugriff)")
    print(ok(mac.obsidian_configured()), "obsidian_vault (Obsidian-Notizen; optional)")
    try:
        import pypdf  # noqa: F401
        print(ok(True), "pypdf (PDF lesen)")
    except ImportError:
        print(ok(False), "pypdf (PDF lesen: pip install pypdf)")
    servers = mcp_client.load_config()
    print(ok(bool(servers)), f"MCP-Server in {mcp_client.config_path()} ({len(servers)}; optional)")
    if servers:
        mcp_client.tools()
        for line in mcp_client.status():
            print("        MCP", line)
    loaded = skills.load_all()
    imported = sum(1 for s in loaded.values() if s.herkunft == "importiert")
    print("INFO   ", f"{len(loaded)} Skills geladen, davon {imported} importiert")
    if hubspot.is_configured():
        try:
            print("        HubSpot-Verbindung:", hubspot.kontakt_suchen("a", 1)[:80])
        except Exception as exc:
            print("        HubSpot-Test fehlgeschlagen:", exc)
    return 0


def skills_cli(args) -> int:
    from . import skill_import, skills

    try:
        if args.skill_quelle:
            path = skill_import.sync(args.skill_quelle)
            sid = path.name
            print(f"Quelle geladen: {path} ({len(skill_import.list_source(sid))} Skills)")
        if args.skills_suchen is not None:
            hits = skill_import.search(args.skills_suchen)
            if not hits:
                print("Keine Treffer. Erst eine Quelle laden: python -m jarvis --skill-quelle hermes")
            for f in hits[:80]:
                print(f"{f.quelle}:{f.name}  [{f.kategorie}]  {f.beschreibung[:110]}")
        if args.skill_installieren:
            print(skill_import.install(args.skill_installieren, force=args.trotzdem))
        if args.skill_entfernen:
            print(skill_import.remove(args.skill_entfernen))
        if args.skills:
            for s in skills.load_all().values():
                print(f"{s.name:28} {s.herkunft:12} {s.description[:90]}")
    except Exception as exc:  # klare Meldung statt Traceback
        print(f"Fehler: {exc}")
        return 1
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(prog="jarvis", description="Jarvis - dein deutscher KI-Assistent")
    ap.add_argument("--stimme", action="store_true", help="Antworten laut vorlesen")
    ap.add_argument("--mikro", action="store_true", help="Sprechen: Enter druecken, dann reden")
    ap.add_argument("--freihaendig", action="store_true", help="Dauerhaft zuhoeren; Aktivierung mit 'Jarvis ...'")
    ap.add_argument("--modell", choices=["opus", "sonnet", "haiku"], help="Claude-Modell (Standard sonnet)")
    ap.add_argument("--google-verbinden", metavar="DATEI", nargs="?", const="", help="Google einmalig verbinden")
    ap.add_argument("--check", action="store_true", help="Einrichtung pruefen")
    ap.add_argument("--telegram-verbinden", action="store_true", help="Telegram einmalig verbinden")
    ap.add_argument("--ui", action="store_true", help="Oberflaeche im Browser starten (http://localhost:8765)")
    ap.add_argument("--demo", action="store_true", help="Expliziter Offline-Demomodus fuer die Oberflaeche")
    sk = ap.add_argument_group("Skills (agentskills.io-Standard, aus OpenJarvis)")
    sk.add_argument("--skill-quelle", metavar="QUELLE", help="Skill-Quelle laden/aktualisieren: hermes, openclaw, anthropic oder owner/repo")
    sk.add_argument("--skills-suchen", metavar="BEGRIFF", nargs="?", const="", help="In geladenen Skill-Quellen suchen")
    sk.add_argument("--skill-installieren", metavar="QUELLE:NAME", help="Skill installieren (nur Text, mit Sicherheitsscan)")
    sk.add_argument("--skill-entfernen", metavar="NAME", help="Importierte Skill entfernen")
    sk.add_argument("--skills", action="store_true", help="Alle geladenen Skills auflisten")
    sk.add_argument("--trotzdem", action="store_true", help="Installation trotz Sicherheitswarnung (nur nach eigener Pruefung)")
    args = ap.parse_args()
    if args.demo:
        os.environ["JARVIS_DEMO"] = "true"

    if args.check:
        sys.exit(check())
    if args.skills or args.skill_quelle or args.skills_suchen is not None or args.skill_installieren or args.skill_entfernen:
        sys.exit(skills_cli(args))
    if args.telegram_verbinden:
        from .tools import telegram

        telegram.connect()
        return
    if args.google_verbinden is not None:
        from .tools import google

        google.connect(args.google_verbinden or None)
        return
    ui_demo = args.ui and str(config.get("demo", "false")).lower() in {"true", "1", "yes"}
    if not ui_demo and not (os.getenv("ANTHROPIC_API_KEY") or config.get("anthropic_key")):
        sys.exit("ANTHROPIC_API_KEY fehlt. Im Terminal: export ANTHROPIC_API_KEY='dein-schluessel'")
    if config.get("anthropic_key") and not os.getenv("ANTHROPIC_API_KEY"):
        os.environ["ANTHROPIC_API_KEY"] = config.get("anthropic_key") or ""

    if args.ui:
        if args.modell:
            os.environ["JARVIS_MODEL"] = args.modell
        from .webui import serve

        serve()
        return

    from .agent import Agent
    from .voice import Voice

    speak = args.stimme or args.mikro or args.freihaendig
    voice = Voice(muted=not speak)
    agent = Agent(channel="stimme" if speak else "text", model=args.modell)
    ears = None
    if args.mikro or args.freihaendig:
        from .ears import Ears

        ears = Ears()
        ears._whisper()  # Modell vorab laden

    print("JARVIS bereit. 'ende' beendet, 'neu' startet ein frisches Gespraech.")
    if args.freihaendig:
        print("Freihaendig: sag 'Jarvis, ...'. Strg+C beendet.")
    wake = re.compile(r"^\W*(hey |hallo |okay )?(jarvis|dscharvis|jarwis)\b[\s,.:!-]*", re.I)

    while True:
        try:
            if args.freihaendig:
                heard = ears.listen()
                if not heard:
                    continue
                m = wake.match(heard)
                if not m:
                    continue
                text = heard[m.end():].strip() or "Ja?"
                print(f"DU (Sprache) > {text}")
            elif args.mikro:
                input("[Enter druecken und sprechen] ")
                print("[hoere zu ...]")
                text = ears.listen()
                if not text:
                    print("(nichts verstanden)")
                    continue
                print(f"DU (Sprache) > {text}")
            else:
                text = input("DU > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text.lower() in ("ende", "exit", "quit", "tschuess", "tschüss"):
            break
        if text.lower() in ("neu", "reset"):
            agent.reset()
            print("Neues Gespraech.")
            continue
        if text.lower() in ("stopp", "stop"):
            voice.stop()
            continue
        voice.resume()
        try:
            agent.ask(text, on_sentence=voice.say, on_tool=lambda n: print(f"  ... {n}"))
        except KeyboardInterrupt:
            voice.stop()
            print("(unterbrochen)")
        except Exception as exc:
            print(f"[Fehler] {type(exc).__name__}: {exc}")
        voice.wait()


if __name__ == "__main__":
    main()
