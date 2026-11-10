"""LangGraph-Migration: aktiver Ausführer, Freigaben über interrupt/Resume, genau ein Ausführer, Persistenz."""

import types

import pytest

from jarvis import agent as A
from jarvis import approvals, config, graph
from jarvis.tools import Tool, ToolFailure, boolean, string

B = types.SimpleNamespace


class Stream:
    def __init__(self, final, texts=()):
        self.final, self.texts = final, list(texts)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    @property
    def text_stream(self):
        yield from self.texts

    def get_final_message(self):
        return self.final


def say(text):
    return Stream(B(stop_reason="end_turn", content=[B(type="text", text=text)]), [text])


def send_call(cid, an, ok=False):
    return Stream(B(stop_reason="tool_use", content=[B(type="tool_use", id=cid, name="test_senden",
                                                       input={"an": an, "text": "Hallo", "bestaetigt_vom_nutzer": ok})]))


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    graph.reset_for_tests()
    yield
    graph.reset_for_tests()


@pytest.fixture
def sent():
    return []


def make(steps, sent, channel="web", conv="g-test"):
    a = A.Agent(channel=channel, conversation_id=conv)
    a.client = B(beta=B(messages=B(stream=lambda **kw: steps.pop(0))))

    def senden(an, text, bestaetigt_vom_nutzer):
        if not bestaetigt_vom_nutzer:
            raise ToolFailure("nur nach Ja")
        sent.append(an)
        return f"gesendet an {an}"

    tool = approvals.wrap(Tool("test_senden", "Testversand", {"an": string("an"), "text": string("text"),
                                                               "bestaetigt_vom_nutzer": boolean("ja")},
                               senden, ["an", "text", "bestaetigt_vom_nutzer"]))
    a.tools[tool.name] = tool
    return a


def tid(a):
    return graph.thread_id(a.memory_store.installation_scope, a.memory_store.owner_scope, a.channel, a.conversation_id)


def approval_ids(a):
    return [p["approval_id"] for p in graph.service().pending(tid(a))]


def test_freigabe_wartet_per_interrupt_und_klick_fuehrt_genau_einmal_aus(sent):
    a = make([send_call("c1", "kunde@x.de"), say("Soll ich die Mail an kunde@x.de senden?")], sent)
    answer = a.ask("Schreib eine Mail an kunde@x.de")
    assert answer == "Soll ich die Mail an kunde@x.de senden?" and sent == []
    [aid] = approval_ids(a)
    state = graph.service().state(tid(a))
    assert state["status"] == "waiting" and state["action_previews"][aid]["aktion"] == "test_senden"
    assert approvals.approve(aid).startswith("gesendet") and sent == ["kunde@x.de"]
    state = graph.service().state(tid(a))
    assert approval_ids(a) == [] and state["execution_status"] == {aid: "COMPLETED"} and state["status"] == "completed"
    with pytest.raises(ToolFailure):  # doppelter Klick
        approvals.approve(aid)
    assert sent == ["kunde@x.de"]


def test_resume_nur_mit_passender_id_und_unveraendertem_payload(sent):
    a = make([send_call("c1", "a@x.de"), say("Senden?")], sent)
    a.ask("Mail an a@x.de")
    [aid] = approval_ids(a)
    svc = graph.service()
    assert svc.resolve("gibtsnicht", tid(a)) is False
    assert svc.resolve(aid, tid(a)) is False  # Freigabe noch offen: ein Resume ist keine Freigabe
    approvals._items[aid]["status"] = "COMPLETED"
    approvals._items[aid]["schluessel"] = '{"an": "boese@x.de"}'  # Payload nach der Vorschau verändert
    assert svc.resolve(aid, tid(a)) is False and approval_ids(a) == [aid]
    assert sent == []


def test_mehrere_offene_freigaben_einzeln_adressierbar(sent):
    two = Stream(B(stop_reason="tool_use", content=[
        B(type="tool_use", id="c1", name="test_senden", input={"an": "eins@x.de", "text": "Hallo", "bestaetigt_vom_nutzer": False}),
        B(type="tool_use", id="c2", name="test_senden", input={"an": "zwei@x.de", "text": "Hallo", "bestaetigt_vom_nutzer": False})]))
    a = make([two, say("Beide senden?")], sent)
    a.ask("Mail an eins und zwei")
    ids = approval_ids(a)
    assert len(ids) == 2
    first = next(i for i in ids if approvals._items[i]["argumente"]["an"] == "eins@x.de")
    approvals.approve(first)
    assert approval_ids(a) == [i for i in ids if i != first] and sent == ["eins@x.de"]
    approvals.reject(approval_ids(a)[0])
    state = graph.service().state(tid(a))
    assert approval_ids(a) == [] and sorted(state["execution_status"].values()) == ["CANCELLED", "COMPLETED"]
    assert sent == ["eins@x.de"]


def test_neue_eingabe_schliesst_offenen_zweig_aber_karte_bleibt_klickbar(sent):
    a = make([send_call("c1", "k@x.de"), say("Senden?"), say("Gern, das Wetter ist schön.")], sent)
    a.ask("Mail an k@x.de")
    [aid] = approval_ids(a)
    a.ask("Wie ist das Wetter?")
    assert approval_ids(a) == [] and approvals._items[aid]["status"] == "WAITING_FOR_APPROVAL"
    approvals.approve(aid)  # späterer Klick: Dispatcher führt genau einmal aus, kein Graph-Resume nötig
    assert sent == ["k@x.de"]


def test_ja_im_naechsten_zug_kein_doppelter_versand(sent):
    a = make([send_call("c1", "k@x.de"), say("Senden?"), send_call("c2", "k@x.de", ok=True), say("Gesendet.")], sent)
    a.ask("Mail an k@x.de")
    [aid] = approval_ids(a)
    a.ask("Ja, senden")
    assert sent == ["k@x.de"] and approvals._items[aid]["status"] == "COMPLETED"
    assert graph.service().resolve(aid, tid(a)) is False and sent == ["k@x.de"]  # kein erneuter Versand


def test_offene_freigabe_ueberlebt_neustart(sent):
    a = make([send_call("c1", "n@x.de"), say("Senden?")], sent)
    a.ask("Mail an n@x.de")
    [aid] = approval_ids(a)
    graph.reset_for_tests()  # Prozess-Neustart: neuer Service, gleiche Checkpoint-Datei
    assert approval_ids(a) == [aid]
    approvals.approve(aid)
    assert approval_ids(a) == [] and sent == ["n@x.de"]


def test_legacy_rueckweg_nutzt_keinen_graph(sent, monkeypatch):
    monkeypatch.setenv("JARVIS_ORCHESTRATOR", "legacy")
    a = make([say("Hallo aus der alten Schleife.")], sent)
    assert a.ask("Hallo") == "Hallo aus der alten Schleife."
    assert graph._service is None  # Graph wurde in diesem Zug gar nicht gestartet


def test_threads_getrennt_und_scope_validiert(sent):
    voice = make([say("ok")], sent, channel="web", conv="haupt")
    bg = make([say("ok")], sent, channel="hintergrund", conv="haupt")
    assert tid(voice) != tid(bg)
    with pytest.raises(ValueError):
        graph.thread_id("default", "local", "web", "../../fremd")


def test_schrittgrenze_bei_kurzem_dialog(sent):
    loop = [Stream(B(stop_reason="tool_use", content=[B(type="tool_use", id=f"t{i}", name="lies_skill",
                                                         input={"name": "x"})])) for i in range(6)]
    a = make(loop, sent)
    assert a.ask("Hi") == "Entschuldigung, die Aufgabe konnte ich nicht abschliessen."
    assert graph.service().state(tid(a))["errors"][-1].startswith("Schrittgrenze 4")


def test_checkpoints_ohne_schluessel_ohne_sdk_objekte_und_mit_retention(sent, monkeypatch):
    fake_key = "-".join(["sk", "ant", "test", "geheim", "1234567890"])  # zur Laufzeit gebaut (Secret-Scan)
    monkeypatch.setenv("ANTHROPIC_API_KEY", fake_key)
    a = make([say("Eins."), say("Zwei."), say("Drei."), say("Vier.")], sent)
    for q in ("a", "b", "c", "d"):
        a.ask(q)
    svc = graph.service()
    blobs = b"".join(r[0] or b"" for r in svc.conn.execute("SELECT checkpoint FROM checkpoints"))
    assert fake_key.encode() not in blobs and b"SimpleNamespace" not in blobs
    assert (config.data_dir() / graph.CHECKPOINT_FILE).stat().st_mode & 0o077 == 0
    svc.prune(keep=3)
    assert svc.conn.execute("SELECT COUNT(*) FROM checkpoints WHERE thread_id=?", (tid(a),)).fetchone()[0] == 3
    assert svc.state(tid(a))["answer"] == "Vier."  # neuester Stand bleibt erhalten


def test_einstufung_bestimmt_route():
    assert graph.classify("Hallo, wie geht's?", "web")["route"] == "direct"
    c = graph.classify("Bewerte den Upwork-Job und schreib eine Bewerbung", "web")
    assert c["route"] == "task" and c["intent"] == "akquise"
    assert graph.classify("hi", "hintergrund")["route"] == "task"


class Boom:
    def __enter__(self):
        raise RuntimeError("Verbindung weg")

    def __exit__(self, *a):
        pass


def test_absturz_mitten_im_lauf_wiederholt_nichts_automatisch(sent):
    calls = []
    a = make([Stream(B(stop_reason="tool_use", content=[B(type="tool_use", id="r1", name="lies_skill",
                                                           input={"name": "x"})])), Boom(), say("Neu gestartet.")], sent)
    real = a._run_tools
    a._run_tools = lambda c, *r: (calls.append([x.name for x in c]), real(c, *r))[1]
    with pytest.raises(RuntimeError):
        a.ask("Lies den Skill")
    graph.reset_for_tests()  # Neustart nach dem Absturz
    assert a.ask("Weiter bitte") == "Neu gestartet." and calls == [["lies_skill"]]  # nichts doppelt gelesen
    assert graph.service().state(tid(a))["status"] == "completed"


def test_budgetgrenze_greift_im_graph(sent, monkeypatch):
    monkeypatch.setenv("JARVIS_JOB_TOKEN_BUDGET", "10")
    usage = B(input_tokens=50, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0)
    a = make([Stream(B(stop_reason="end_turn", content=[B(type="text", text="Lang.")], usage=usage), ["Lang."])], sent)
    assert "Token-Budget" in a.ask("Analysiere alles")
