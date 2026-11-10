"""Etappe 5b: akustische Aktivierung – Pipeline mit Software-Fixtures (keine echte Erkennungsleistung!).

Geprüft wird: Impuls-Vorfilter, Entscheidung mit Schwelle und Gegenklassen-Abstand, Dedupe/Cooldown, Doppelmodus,
Kalibrierung, Mikrofon-Übergabe bei Sprache, Unterdrückung eigener Ausgabe, Pause, Gerätefehler, stummes Signal,
fehlendes Modell, Standard AUS, Öffnen/Fokussieren mit Sperrbildschirm, Popup-Schutz und Singleton-Start.
Die echte Trefferquote im Raum ist NICHT durch diese Tests belegt (NOT_RUN bis zur Kalibrierung am Gerät).
"""

import fcntl

import numpy as np
import pytest

from jarvis.activation import detector, launcher
from jarvis.activation.capture import Ring
from jarvis.activation.detector import FRAME, RATE, Debouncer, ImpulseFilter, calibrate, decide
from jarvis.activation.service import Service
from jarvis.activation.settings import Settings

rng = np.random.default_rng(7)


def clap(level=0.6) -> np.ndarray:
    t = np.arange(int(0.25 * RATE)) / RATE
    return (rng.standard_normal(len(t)) * np.exp(-t / 0.012) * level).astype(np.float32)


def quiet(seconds: float, level=0.002) -> np.ndarray:
    return (rng.standard_normal(int(seconds * RATE)) * level).astype(np.float32)


def onsets(signal: np.ndarray) -> list[int]:
    f, out = ImpulseFilter(), []
    for i in range(0, len(signal) - FRAME + 1, FRAME):
        o = f.push(signal[i:i + FRAME])
        if o is not None:
            out.append(o)
    return out


def test_vorfilter_findet_impuls_aber_nicht_dauerton_oder_leises_klicken():
    sig = np.concatenate([quiet(1.0), clap(), quiet(1.0)])
    found = onsets(sig)
    assert len(found) == 1 and abs(found[0] - RATE) < 3 * FRAME
    hum = np.concatenate([quiet(0.5), (np.sin(np.arange(RATE) / 3) * 0.3).astype(np.float32), quiet(0.5)])
    assert onsets(hum) == []  # lauter, aber anhaltend (Sprache/Musik-artig): kein Impuls
    clicks = quiet(2.0)
    for i in range(10):
        clicks[int(0.15 * i * RATE): int(0.15 * i * RATE) + 30] += 0.01  # leises Tippen
    assert onsets(clicks) == []


def test_entscheidung_braucht_schwelle_und_abstand_zur_gegenklasse():
    th = {"clap": 0.3, "snap": 0.3}
    assert decide({"clap": 0.6, "snap": 0.1, "_negative": 0.2, "_negative_name": "Speech"}, ["clap", "snap"], th, 0.1).trigger == "clap"
    typing = decide({"clap": 0.5, "snap": 0.0, "_negative": 0.45, "_negative_name": "Typing"}, ["clap", "snap"], th, 0.1)
    assert not typing.accepted and typing.negative_name == "Typing"
    assert not decide({"clap": 0.2, "snap": 0.0, "_negative": 0.0}, ["clap"], th, 0.1).accepted
    assert not decide({"clap": 0.0, "snap": 0.9, "_negative": 0.0}, ["clap"], th, 0.1).accepted  # Schnipsen abgewählt


def test_dedupe_cooldown_und_doppelmodus():
    d = Debouncer(3.0)
    assert d.offer(10.0, 10.0) is not None
    assert d.offer(10.1, 10.2) is None   # gleicher Impuls, überlappendes Fenster
    assert d.offer(11.0, 11.0) is None   # Cooldown
    assert d.offer(14.0, 14.0) is not None
    dd = Debouncer(3.0, mode="double")
    assert dd.offer(1.0, 1.0) is None and dd.offer(1.5, 1.5) is not None  # zwei innerhalb 0,9 s
    assert dd.offer(10.0, 10.0) is None and dd.offer(12.0, 12.0) is None  # zu weit auseinander


def test_kalibrierung_liegt_zwischen_stoerung_und_eigenen_treffern():
    assert 0.12 <= calibrate([0.7, 0.8, 0.65], [0.1, 0.2]) <= 0.6
    assert calibrate([0.7, 0.8], [0.45]) >= 0.45 * 1.15 - 1e-9
    assert calibrate([], []) == 0.3


# --- Dienst mit Fake-Mikrofon und Fake-Klassifikator -------------------------------------------------------

class FakeCapture:
    def __init__(self, ok=True):
        self.ok, self.active, self.error, self.starts, self.stops = ok, False, None, 0, 0

    def start(self):
        self.starts += 1
        self.active = self.ok
        self.error = None if self.ok else "PortAudioError: kein Gerät"
        return self.ok

    def stop(self):
        self.stops += 1
        self.active = False


class FakeClassifier:
    def __init__(self, groups):
        self.groups, self.calls = groups, 0

    def group_scores(self, window):
        self.calls += 1
        return dict(self.groups)


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def make(groups=None, audio=None, **settings):
    s = Settings(enabled=True, **settings)
    ring, clock, fired = Ring(), Clock(), []
    state = {"audio": audio or {}}
    svc = Service(s, FakeClassifier(groups or {"clap": 0.8, "snap": 0.1, "_negative": 0.1, "_negative_name": "Speech"}),
                  ring=ring, capture=FakeCapture(), audio_state=lambda: state["audio"],
                  act=lambda url: fired.append(url) or "fokussiert", clock=clock)
    return svc, ring, clock, fired, state


def feed(svc, ring, clock, signal, step_s=0.1):
    for i in range(0, len(signal), int(step_s * RATE)):
        ring.write(signal[i:i + int(step_s * RATE)])
        clock.t += step_s
        svc.step()
    for t in list(__import__("threading").enumerate()):
        if t.name.startswith("Thread") and t.is_alive():
            t.join(0.5)


def test_ein_klatschen_oeffnet_genau_einmal_dann_cooldown():
    svc, ring, clock, fired, _ = make()
    svc.step()  # Mikrofon öffnen
    feed(svc, ring, clock, np.concatenate([quiet(0.5), clap(), quiet(1.5), clap(), quiet(1.5)]))
    assert fired == ["http://localhost:8080"] and svc.stats.ausgeloest == 1 and svc.stats.erkannt == 2
    assert svc.stats.letzte_latenz_ms is not None


def test_stoergeraeusch_wird_abgelehnt():
    svc, ring, clock, fired, _ = make(groups={"clap": 0.5, "snap": 0.0, "_negative": 0.48, "_negative_name": "Typing"})
    svc.step()
    feed(svc, ring, clock, np.concatenate([quiet(0.5), clap(), quiet(1.5)]))
    assert fired == [] and svc.stats.abgelehnt == 1 and svc.stats.letzte_bewertung["gegenklasse"] == "Typing"


def test_waehrend_jarvis_spricht_mikrofon_frei_und_keine_ausloesung():
    svc, ring, clock, fired, state = make()
    svc.step()
    assert svc.capture.active
    state["audio"] = {"tts_aktiv": True}
    svc.step()
    assert svc.state == "SPRACHE_AKTIV" and not svc.capture.active  # Mikrofon wirklich freigegeben
    feed(svc, ring, clock, np.concatenate([quiet(0.3), clap(), quiet(1.2)]))
    assert fired == []
    state["audio"] = {}
    clock.t += 6
    svc.step()
    assert svc.capture.active and svc.state == "BEREIT"


def test_eigene_ausgabe_zum_entscheidungszeitpunkt_wird_unterdrueckt():
    svc, ring, clock, fired, state = make()
    calls = {"n": 0}

    def audio():  # Sprache startet genau, während das Fenster ausgewertet wird
        calls["n"] += 1
        return {"tts_aktiv": True} if svc.classifier.calls > 0 else {}
    svc.audio_state = audio
    svc.step()
    feed(svc, ring, clock, np.concatenate([quiet(0.3), clap(), quiet(1.2)]))
    assert fired == [] and svc.stats.unterdrueckt == 1


def test_pause_gibt_mikrofon_frei_und_fortsetzen():
    svc, ring, clock, fired, _ = make()
    svc.step()
    svc.pause()
    assert svc.state == "PAUSIERT" and not svc.capture.active
    feed(svc, ring, clock, np.concatenate([quiet(0.3), clap(), quiet(1.2)]))
    assert fired == [] and svc.capture.starts == 1
    svc.resume()
    svc.step()
    assert svc.capture.active


def test_geraetefehler_stummes_signal_modell_fehlt_und_standard_aus():
    svc, ring, clock, fired, _ = make()
    svc.capture = FakeCapture(ok=False)
    svc.step()
    assert svc.state == "KEIN_GERAET"
    clock.t += 1
    svc.step()
    assert svc.capture.starts == 1  # erst nach 5 s neuer Versuch
    clock.t += 5
    svc.step()
    assert svc.capture.starts == 2
    svc2, ring2, clock2, _, _ = make()
    svc2.step()
    feed(svc2, ring2, clock2, np.zeros(int(17 * RATE), np.float32), step_s=0.5)
    assert svc2.state == "KEIN_SIGNAL"
    svc3, *_ = make()
    svc3.classifier = None
    svc3.step()
    assert svc3.state == "KEIN_MODELL" and not svc3.capture.active
    off = Service(Settings(), FakeClassifier({}), ring=Ring(), capture=FakeCapture(), audio_state=lambda: {})
    off.step()
    assert off.state == "AUS" and off.capture.starts == 0 and Settings().enabled is False


def test_einstellungen_nur_lokale_adresse(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    (tmp_path / "aktivierung.json").write_text('{"enabled": true, "url": "https://boese.example", "mode": "x", '
                                               '"triggers": ["pfeifen"], "cooldown_ms": 5}')
    s = Settings.load()
    assert s.url == "http://localhost:8080" and s.mode == "single" and s.triggers == ["clap", "snap"]
    assert s.cooldown_ms == 1000


# --- Öffnen / Fokussieren -----------------------------------------------------------------------------

@pytest.fixture
def actions(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    log = {"open": [], "start": 0, "focus": True, "healthy": True, "locked": False}
    monkeypatch.setattr(launcher, "_last_open", 0.0)
    monkeypatch.setattr(launcher, "screen_locked", lambda: log["locked"])
    monkeypatch.setattr(launcher, "healthy", lambda timeout=1.5: log["healthy"])
    monkeypatch.setattr(launcher, "focus_tab", lambda url: log["focus"])

    def fake_run(cmd, **kw):
        if cmd[0] == "open":
            log["open"].append(cmd[1])
        return type("R", (), {"stdout": "", "returncode": 0})()

    class FakePopen:
        def __init__(self, cmd, **kw):
            log["start"] += 1
            log["cmd"] = cmd
            log["healthy"] = True

    monkeypatch.setattr(launcher.subprocess, "run", fake_run)
    monkeypatch.setattr(launcher.subprocess, "Popen", FakePopen)
    return log


def test_gesperrter_bildschirm_zeigt_nichts(actions):
    actions["locked"] = True
    assert launcher.bring_up("http://localhost:8080") == "gesperrt" and actions["open"] == [] and actions["start"] == 0


def test_vorhandenen_tab_fokussieren_sonst_einmal_oeffnen(actions):
    assert launcher.bring_up("http://localhost:8080") == "fokussiert" and actions["open"] == []
    actions["focus"] = False
    assert launcher.bring_up("http://localhost:8080") == "geoeffnet"
    assert launcher.bring_up("http://localhost:8080") == "schon_geoeffnet"  # keine Popup-Flut
    assert actions["open"] == ["http://localhost:8080"]


def test_beendeter_jarvis_wird_ueber_festen_starter_gestartet(actions):
    actions["healthy"] = False
    actions["focus"] = False
    assert launcher.bring_up("http://localhost:8080") == "gestartet_geoeffnet"
    assert actions["start"] == 1 and actions["cmd"][0].endswith("jarvis-start.sh") and actions["cmd"][1] == "hintergrund"


def test_singleton_sperre_verhindert_zweiten_start(actions, tmp_path):
    actions["healthy"] = False
    with open(tmp_path / "aktivierung.lock", "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)  # anderer Start läuft gerade
        assert launcher.start_runtime(wait_s=0.1) is False
    assert actions["start"] == 0


def test_echtes_modell_reagiert_nicht_auf_stille():
    from pathlib import Path

    d = Path.home() / ".jarvis" / "models"
    if not (d / "yamnet.tflite").exists():
        pytest.skip("YAMNet erst nach dem Setup vorhanden (NOT_RUN)")
    c = detector.Classifier(str(d / "yamnet.tflite"), str(d / "yamnet_class_map.csv"))
    g = c.group_scores(np.zeros(detector.WINDOW, np.float32))
    assert not decide(g, ["clap", "snap"], {"clap": 0.3, "snap": 0.3}, 0.1).accepted
