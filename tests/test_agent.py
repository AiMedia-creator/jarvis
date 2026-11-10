import threading
import time
import types

import pytest

from jarvis import agent as A

B = types.SimpleNamespace


class Stream:
    def __init__(self, final, texts, delay=0.0):
        self.final, self.texts, self.delay = final, texts, delay

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    @property
    def text_stream(self):
        for t in self.texts:
            time.sleep(self.delay)
            yield t

    def get_final_message(self):
        return self.final


def make_agent(steps):
    a = A.Agent(channel="stimme")
    a.client = B(beta=B(messages=B(stream=lambda **kw: steps.pop(0))))
    return a


def test_werkzeug_schleife_und_saetze():
    steps = [Stream(B(stop_reason="tool_use", content=[B(type="tool_use", id="t1", name="lies_skill",
                                                         input={"name": "hubspot-vertrieb"})]), []),
             Stream(B(stop_reason="end_turn", content=[B(type="text", text="Fertig. Zweiter Satz.")]),
                    ["Fertig. ", "Zweiter Satz."])]
    a = make_agent(steps)
    said, tools = [], []
    a.ask("test", on_sentence=said.append, on_tool=tools.append)
    assert said == ["Fertig.", "Zweiter Satz."] and tools == ["lies_skill"]
    assert "Nachschlagen" in a.messages[2]["content"][0]["content"]


def test_abbruch_behaelt_kontext():
    texts = ["Eins. ", "Zwei. ", "Drei. ", "Vier."]
    final = B(stop_reason="end_turn", content=[B(type="text", text="".join(texts))])
    a = make_agent([Stream(final, texts, delay=0.2)])
    t = threading.Thread(target=lambda: pytest.raises(A.Cancelled, a.ask, "Erzähl"))
    t.start()
    time.sleep(0.5)
    a.cancel()
    t.join(5)
    assert a.messages[-1]["content"].endswith("[vom Nutzer unterbrochen]")
    assert a.messages[-2] == {"role": "user", "content": "Erzähl"}


def test_gedaechtnis_im_prompt(tmp_path):
    from jarvis import config

    mem = config.data_dir() / "memories"
    mem.mkdir(exist_ok=True)
    (mem / "kunden.md").write_text("Max Muster ist wichtigster Kunde.", encoding="utf-8")
    a = A.Agent(channel="web")
    a._append_user_turn("Wer ist mein wichtigster Kunde?")
    req = a._request()
    system = req["system"][0]["text"]
    # Gedächtnis steht im Nutzerzug (dynamisch), nicht im gecachten Systemprompt
    assert "Max Muster ist wichtigster Kunde." in req["messages"][-1]["content"]
    assert "Max Muster" not in system and "@@MEMORY@@" not in system


def test_workspace_header(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", "wrkspc_test")
    a = A.Agent(channel="text")
    assert a.client._custom_headers.get("anthropic-workspace-id") == "wrkspc_test"
