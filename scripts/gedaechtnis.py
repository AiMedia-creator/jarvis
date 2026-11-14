#!/usr/bin/env python3
"""Gedächtnis sichern, prüfen, exportieren und (optional) in gbrain spiegeln. Die Quelldatenbank wird nur gelesen.

  python scripts/gedaechtnis.py sichern               geprüfte Sicherung nach ~/.jarvis/backups/gedaechtnis-<Zeit>/
  python scripts/gedaechtnis.py pruefen ORDNER        Sicherung erneut gegen ihr Manifest prüfen (inkl. Zeilenzahlen)
  python scripts/gedaechtnis.py probe ORDNER          Wiederherstellung in einem temporären Ordner proben und vergleichen
  python scripts/gedaechtnis.py exportieren           bestätigte Erinnerungen als Markdown nach ~/.jarvis/gbrain-export/
  python scripts/gedaechtnis.py rueckweg              Export in eine neue Test-Datenbank zurückspielen und vergleichen
  python scripts/gedaechtnis.py gbrain                gbrain lokal einrichten (ohne Embeddings) und Export importieren
  python scripts/gedaechtnis.py status                Stand von Sicherung, Export und gbrain
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jarvis import gbrain_bruecke as g  # noqa: E402


def main(argv: list[str]) -> int:
    befehl = argv[1] if len(argv) > 1 else "status"
    try:
        if befehl == "sichern":
            out = g.sichern()
        elif befehl == "pruefen":
            out = g.sicherung_pruefen(Path(argv[2]))
        elif befehl == "probe":
            out = g.wiederherstellung_pruefen(Path(argv[2]))
        elif befehl == "exportieren":
            out = g.exportieren(mit_archiv="--mit-archiv" in argv)
        elif befehl == "rueckweg":
            with tempfile.TemporaryDirectory(prefix="jarvis-rueckweg-") as tmp:
                export = g.exportieren(Path(tmp) / "export")
                g.zurueckspielen(Path(export["ordner"]), Path(tmp) / "neu.sqlite3")
                out = {"export": export["erinnerungen"], **g.vergleichen(g._db_pfad(), Path(tmp) / "neu.sqlite3")}
        elif befehl == "gbrain":
            out = {"einrichten": g.gbrain_einrichten(), **g.gbrain_importieren()}
        elif befehl == "status":
            out = g.status()
        else:
            print(__doc__)
            return 2
    except (g.BrueckeFehler, IndexError, OSError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("ok", out.get("gleich", True)) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
