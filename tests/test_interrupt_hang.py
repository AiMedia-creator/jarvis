"""Unterbrechen darf nie zu Hängern führen: hängender Claude-Stream, überholte Anfragen, Job-Status."""

import threading
import time
import types

import pytest

from jarvis import agent as A
from jarvis import jobs

B = types.SimpleNamespace


class HangingStream:
    """Liefert ein Token und blockiert dann wie ein hängender Socket-Read (lässt sich nicht von außen beenden)."""

    def __init__(self):
        self.released = threading.Event()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def close(self):
        pass  # wie in der Praxis: Schließen von außen beendet den blockierenden Read NICHT

    @property
    def text_stream(self):
        yield "Ich erzähle "
        self.released.wait(30)

    def get_final_message(self):
        return B(stop_reason="end_turn", content=[B(type="text", text="x")], usage=None)


class QuickStream:
    def __init__(self, text):
        self.text = text

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def close(self):
        pass

    @property
    def text_stream(self):
        yield self.text

    def get_final_message(self):
        return B(stop_reason="end_turn", content=[B(type="text", text=self.text)], usage=None)


def test_nach_unterbrechung_antwortet_jarvis_sofort_trotz_haengendem_stream():
    hanging = HangingStream()
    streams = [hanging, QuickStream("Es ist kurz nach eins.")]
    a = A.Agent(channel="web", conversation_id="haenger-test")
    a.client = B(beta=B(messages=B(stream=lambda **kw: streams.pop(0))))
    first = threading.Thread(target=lambda: pytest.raises(A.Cancelled, a.ask, "Erzähl mir lange etwas"))
    first.start()
    time.sleep(0.3)
    t0 = time.time()
    answer = a.ask("Stopp. Wie spät ist es?")  # bricht die hängende Antwort ab
    elapsed = time.time() - t0
    hanging.released.set()
    first.join(5)
    assert answer == "Es ist kurz nach eins." and elapsed < 2.0, f"Folgefrage hing {elapsed:.1f} s"


def test_ueberholte_anfrage_bricht_die_neuere_nicht_ab_und_job_wird_beendet():
    a = A.Agent(channel="web", conversation_id="ueberholt-test")
    a.client = B(beta=B(messages=B(stream=lambda **kw: QuickStream("Neu."))))
    stale = threading.Event()
    stale.set()  # die Brücke hat diese Anfrage schon als überholt markiert
    old_job = jobs.create("alt", "ueberholt-test")
    with pytest.raises(A.Cancelled):
        a.ask("alte Frage", cancel_signal=stale, job=old_job)
    assert old_job["status"] == jobs.CANCELLED
    assert not a.cancel_event.is_set()  # die überholte Anfrage hat nichts angefasst
    assert a.ask("neue Frage", cancel_signal=threading.Event()) == "Neu."
