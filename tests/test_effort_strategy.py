"""Denktiefe je Kanal, Ausweichen bei Modellen ohne effort und das Strategie-Werkzeug (ohne echte API)."""

import types

import anthropic
import httpx
import pytest

from jarvis import agent as A
from jarvis import strategy
from jarvis.tools import ToolFailure

B = types.SimpleNamespace


@pytest.fixture(autouse=True)
def no_effort_env(monkeypatch):
    monkeypatch.delenv("JARVIS_EFFORT", raising=False)


def test_aktuelle_modelle_und_denktiefe_je_kanal():
    assert A.MODELS == {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5", "haiku": "claude-haiku-5-5"}
    assert A.effort_for("claude-sonnet-5-5", "web") == "low"
    assert A.effort_for("claude-sonnet-5-5", "hintergrund") == "medium"
    assert A.effort_for("claude-haiku-4-5-20251001", "web") is None  # ältere Modelle: kein effort


def test_effort_ueberschreibbar(monkeypatch):
    monkeypatch.setenv("JARVIS_EFFORT", "high")
    assert A.effort_for("claude-opus-5-5", "web") == "high"
    monkeypatch.setenv("JARVIS_EFFORT", "turbo")
    assert A.effort_for("claude-opus-5-5", "web") == "medium"  # ungültig -> sicherer Standard


def test_request_enthaelt_effort_und_genug_tokens():
    a = A.Agent(channel="web")
    a.model = "claude-sonnet-5-5"
    req = a._request()
    assert req["output_config"] == {"effort": "low"} and req["max_tokens"] >= 16000


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


def test_modell_ohne_effort_wird_ohne_neu_versucht():
    calls = []
    final = B(stop_reason="end_turn", content=[B(type="text", text="Hallo.")], usage=None)

    def stream(**kw):
        calls.append(kw)
        if "output_config" in kw:
            response = httpx.Response(400, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
            raise anthropic.BadRequestError("output_config.effort: not supported for this model", response=response, body=None)
        return Stream(final, ["Hallo."])

    a = A.Agent(channel="web")
    a.client = B(beta=B(messages=B(stream=stream)))
    assert a.ask("Hi") == "Hallo."
    assert "output_config" in calls[0] and "output_config" not in calls[1]


class FakeMessages:
    def __init__(self, fail_roles=()):
        self.fail_roles, self.calls = fail_roles, []

    def create(self, **kw):
        self.calls.append(kw)
        role = kw["system"].split("Deine Perspektive (")[1].split(")")[0]
        if role in self.fail_roles:
            raise RuntimeError("boom")
        return B(content=[B(type="text", text=f"Befund {role}")])


def test_strategie_gremium_drei_perspektiven(monkeypatch):
    fake = FakeMessages()
    monkeypatch.setattr(A, "make_client", lambda timeout=60.0: B(messages=fake))
    out = strategy.analyze("Soll ich die Preise erhöhen?", "Umsatz Q3: unbekannt")
    assert out.count("Befund") == 3 and "OPERATOR" in out and "CONTRARIAN" in out
    assert all(call["max_tokens"] >= 1500 and call["output_config"] == {"effort": "low"} for call in fake.calls)


def test_strategie_teilausfall_und_totalausfall(monkeypatch):
    fake = FakeMessages(fail_roles={"Visionär"})
    monkeypatch.setattr(A, "make_client", lambda timeout=60.0: B(messages=fake))
    out = strategy.analyze("Neuer Vertriebskanal?")
    assert "Nicht verfügbar" in out and "Befund Operator" in out
    fake_all = FakeMessages(fail_roles={role for role, _ in strategy.ROLES})
    monkeypatch.setattr(A, "make_client", lambda timeout=60.0: B(messages=fake_all))
    with pytest.raises(ToolFailure, match="nicht erreichbar"):
        strategy.analyze("Neuer Vertriebskanal?")
    with pytest.raises(ToolFailure):
        strategy.analyze("   ")


class _Stream:
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


def test_execute_mode_ist_ein_gespraechszustand():
    a = A.Agent(channel="web", conversation_id="execute-test")
    a.client = B(beta=B(messages=B(stream=lambda **kw: _Stream("Ok."))))
    last_user = lambda: [m for m in a.messages if m["role"] == "user"][-1]["content"]
    a.ask("Jarvis, Execute Mode")
    assert "EXECUTE MODE aktiv" in last_user()
    a.ask("Was mache ich mit dem Vertrieb?")
    assert "EXECUTE MODE aktiv" in last_user()  # bleibt aktiv
    a.messages.clear()  # auch nach Kürzen des Verlaufs
    a.ask("Und jetzt?")
    assert "EXECUTE MODE aktiv" in last_user()
    a.ask("Zurück in den Advisor Mode bitte")
    assert "EXECUTE MODE aktiv" not in last_user()


def test_lead_manager_bewertet_und_bleibt_ehrlich_und_sicher():
    p = A.SYSTEM_PROMPT
    assert "Lead-Score (1 bis 10)" in p and "PRIORITY LEAD" in p and "Hook" in p and "Call to Action" in p
    assert "Sofort verwerfen" in p and "Pattern Interrupt" in p and "Authority Gap" in p and "Risk Reversal" in p
    assert "ohne Garantien" in p  # Risk Reversal ohne erfundene Versprechen
    assert "[ECHTES BEISPIEL EINFUEGEN]" in p and "nie Kunden, Zahlen oder Erfolge" in p  # kein erfundener Proof
    assert "keine\n\"ACTION:\"-Befehle" in p and "seite_oeffnen" in p  # keine vorgetäuschten Aktionen
    # Wahrheits- und Freigaberegeln bleiben erhalten
    assert "# Wahrheit und Quellen" in p and "# Freigaben fuer Aktionen nach aussen" in p


def test_drive_architekt_schlaegt_vor_und_loescht_nie():
    p = A.SYSTEM_PROMPT
    assert "# Digital-Architekt (Google Drive)" in p and "drive_uebersicht" in p and "drive_ordnen" in p
    assert "Loeschen kannst und darfst du nicht" in p and "05_ARCHIV" in p


def test_design_engine_mit_vier_phasen_und_ehrlichem_radar():
    p = A.SYSTEM_PROMPT
    assert "# Design- und Produkt-Engine" in p and "Blueprint (Was)" in p and "Skalierung (Wachsen)" in p
    assert "Midjourney" in p and "in_zwischenablage" in p and "Strategischer Radar" in p
    assert "du gibst dich nie als diese Personen aus" in p and "mit Quelle und Datum" in p


def test_saas_gremium_mit_eigenen_rollen(monkeypatch):
    fake = FakeMessages()
    monkeypatch.setattr(A, "make_client", lambda timeout=60.0: B(messages=fake))
    out = strategy.analyze("Wie mache ich aus Jarvis ein SaaS?", board="saas")
    assert "GROWTH HACKER" in out and "TECHNISCHER ARCHITEKT" in out and "SALES CLOSER" in out and "VISIONÄR" not in out
    t = strategy.tool()
    assert t.properties["gremium"]["enum"] == ["gruender", "saas"]
    assert "VISIONÄR" in t.handler(frage="Preise?")  # Standard bleibt das Gründer-Gremium
    p = A.SYSTEM_PROMPT
    assert 'gremium="saas"' in p and "importierbares JSON" in p and "lokale Ein-Nutzer-App" in p
    assert "Schluessel gehoeren nie\nin einen Workflow" in p
