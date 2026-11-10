"""Hilfen: echte Agenten mit simuliertem Claude (Antwort haengt von der Frage ab)."""

import threading
import types

from jarvis import agent as A

B = types.SimpleNamespace


class Stream:
    def __init__(self, final, texts, delay=0.0):
        self.final, self.texts, self.delay = final, texts, delay
        self.closed = threading.Event()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def close(self):
        self.closed.set()

    @property
    def text_stream(self):
        for t in self.texts:
            if self.closed.wait(self.delay):
                raise RuntimeError("stream closed")
            yield t

    def get_final_message(self):
        return self.final


def scripted(req_text: str):
    """Antwortet je nach Frage; 'langsam' = 20 Tokens a 0,1 s; Antworttext enthaelt das Stichwort der Frage."""
    word = req_text.split()[-1].strip("?.!")
    if "langsam" in req_text:
        texts = [f"{word}-{i} " for i in range(20)]
        return Stream(B(stop_reason="end_turn", content=[B(type="text", text="".join(texts))]), texts, delay=0.1)
    text = f"Antwort zu {word}."
    return Stream(B(stop_reason="end_turn", content=[B(type="text", text=text)]), [f"Antwort zu ", f"{word}."])


def make_agent(channel="web", conversation_id="haupt", script=scripted):
    a = A.Agent(channel=channel, conversation_id=conversation_id)

    def stream(**kw):
        last = kw["messages"][-1]["content"]
        text = last if isinstance(last, str) else ""
        if isinstance(last, list):  # Werkzeugergebnisse -> Abschluss
            return Stream(B(stop_reason="end_turn", content=[B(type="text", text="Fertig.")]), ["Fertig."])
        return script(text.split("\n")[-1])

    a.client = B(beta=B(messages=B(stream=stream)))
    return a
