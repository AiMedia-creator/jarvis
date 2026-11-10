"""Leistungs-Telemetrie pro Job: nur Zeiten, IDs, Modell, Werkzeugnamen, Status und Fehlerart – keine Inhalte.

Pro Job: job, conversation, art (vordergrund/hintergrund/echtzeit), modell, ui_feedback_ms, erstes_token_ms,
erster_satz_ms, llm_ende_ms, stimme_ms, gesamt_ms, runden, status, fehlerart, unterbrochen,
tokens_ein, tokens_aus, tokens_cache_lesen, tokens_cache_schreiben und – nur wenn Preise konfiguriert sind – kosten_usd.
Datei: ~/.jarvis/telemetry.jsonl. Abruf: /api/telemetry.

Preise werden bewusst NICHT im Code geraten. Optional: JARVIS_PREISE='{"claude-sonnet-5-5": [ein, aus, cache_lesen,
cache_schreiben]}' in USD pro 1 Mio. Tokens (Werte aus der offiziellen Anthropic-Preisliste).
"""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict, deque

from . import config

_lock = threading.Lock()
_jobs: "OrderedDict[str, dict]" = OrderedDict()
_tools: deque = deque(maxlen=300)
_CLIENT_KEYS = ("ui_feedback_ms", "stimme_ms", "erstes_token_client_ms", "gesamt_client_ms")


def now_ms() -> float:
    return time.perf_counter() * 1000


def _write(entry: dict) -> None:
    try:
        with open(config.data_dir() / "telemetry.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def start_job(job_id: str, conversation_id: str, model: str = "", kind: str = "vordergrund") -> None:
    with _lock:
        _jobs[job_id] = {"job": job_id, "conversation": conversation_id, "art": kind, "modell": model,
                         "start": round(time.time(), 3), "status": "RUNNING", "_t0": now_ms()}
        while len(_jobs) > 100:
            _jobs.popitem(last=False)


def mark(job_id: str, key: str, overwrite: bool = False) -> None:
    """Zeitpunkt relativ zum Jobstart setzen (beim ersten Mal; mit overwrite immer)."""
    with _lock:
        rec = _jobs.get(job_id)
        if rec is not None and (overwrite or key not in rec):
            rec[key] = round(now_ms() - rec["_t0"])


def add(job_id: str, key: str, value) -> None:
    with _lock:
        rec = _jobs.get(job_id)
        if rec is not None:
            rec[key] = value


_USAGE_KEYS = {"input_tokens": "tokens_ein", "output_tokens": "tokens_aus",
               "cache_read_input_tokens": "tokens_cache_lesen", "cache_creation_input_tokens": "tokens_cache_schreiben"}


def _prices(model: str) -> list[float] | None:
    raw = config.get("preise")
    if not raw:
        return None
    try:
        table = json.loads(raw)
        values = [float(v) for v in table.get(model, [])]
    except (ValueError, TypeError, AttributeError):
        return None
    return values if len(values) == 4 else None


def add_usage(job_id: str, model: str, counts: dict) -> None:
    """Tokenverbrauch einer Modellantwort zum Job addieren (und Kosten, falls Preise konfiguriert sind)."""
    with _lock:
        rec = _jobs.get(job_id)
        if rec is None:
            return
        for src, dst in _USAGE_KEYS.items():
            rec[dst] = rec.get(dst, 0) + int(counts.get(src, 0) or 0)
        price = _prices(model)
        if price:
            ein, aus, lesen, schreiben = price
            rec["kosten_usd"] = round(rec.get("kosten_usd", 0.0) + (
                counts.get("input_tokens", 0) * ein + counts.get("output_tokens", 0) * aus
                + counts.get("cache_read_input_tokens", 0) * lesen
                + counts.get("cache_creation_input_tokens", 0) * schreiben) / 1_000_000, 6)


def finish_job(job_id: str, status: str, error: BaseException | str | None = None) -> None:
    with _lock:
        rec = _jobs.get(job_id)
        if rec is None or "gesamt_ms" in rec:
            return
        rec["gesamt_ms"] = round(now_ms() - rec["_t0"])
        rec["status"] = status
        rec["unterbrochen"] = status == "CANCELLED"
        if error is not None:  # nur die Art des Fehlers, keine Nachricht (koennte Inhalte enthalten)
            rec["fehlerart"] = error if isinstance(error, str) else type(error).__name__
        entry = {k: v for k, v in rec.items() if not k.startswith("_")}
    _write(entry)


def client_report(job_id: str, values: dict) -> bool:
    """Messwerte aus dem Browser (UI-Feedback, Start der Stimme). Nur bekannte Zahlenfelder, begrenzt."""
    with _lock:
        rec = _jobs.get(job_id)
        if rec is None:
            return False
        for k in _CLIENT_KEYS:
            v = values.get(k)
            if isinstance(v, (int, float)) and 0 <= v < 600000:
                rec[k] = round(v)
    return True


def tool(job_id: str, name: str, duration_ms: float, status: str) -> None:
    entry = {"ts": round(time.time(), 3), "job": job_id, "art": "werkzeug", "name": name,
             "dauer_ms": round(duration_ms), "status": status}
    with _lock:
        _tools.append(entry)
    _write(entry)


def job(job_id: str) -> dict | None:
    with _lock:
        rec = _jobs.get(job_id)
        return {k: v for k, v in rec.items() if not k.startswith("_")} if rec else None


def summary(last: int = 20) -> dict:
    with _lock:
        recs = [{k: v for k, v in r.items() if not k.startswith("_")} for r in _jobs.values()]
        tools = list(_tools)
    done = [r for r in recs if "gesamt_ms" in r][-last:]

    def avg(key):
        vals = [r[key] for r in done if isinstance(r.get(key), (int, float))]
        return round(sum(vals) / len(vals)) if vals else None

    by_tool: dict[str, list] = {}
    for t in tools:
        by_tool.setdefault(t["name"], []).append(t["dauer_ms"])
    return {
        "jobs": len(done),
        "mittel": {k: avg(k) for k in ("ui_feedback_ms", "erstes_token_ms", "llm_ende_ms", "stimme_ms", "gesamt_ms")},
        "abgebrochen": sum(1 for r in done if r.get("unterbrochen")),
        "fehler": sum(1 for r in done if r.get("status") == "FAILED"),
        "tokens": {dst: sum(int(r.get(dst, 0) or 0) for r in done) for dst in _USAGE_KEYS.values()},
        "kosten_usd": (round(sum(float(r["kosten_usd"]) for r in done if "kosten_usd" in r), 4)
                       if any("kosten_usd" in r for r in done) else None),
        "werkzeuge": {k: {"anzahl": len(v), "mittel_ms": round(sum(v) / len(v)), "max_ms": round(max(v))}
                      for k, v in by_tool.items()},
        "pro_job": list(reversed(recs[-last:])),
    }
