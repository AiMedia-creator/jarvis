"""Dashboard-Status: echte Zustände, keine Zugangsdaten in der Antwort."""

import json

from jarvis import config, uebersicht


def test_status_struktur_und_ehrlichkeit(monkeypatch):
    monkeypatch.setenv("JARVIS_WINDY_API_KEY", "geheimer-test-schluessel")
    data = uebersicht.automations_status()
    assert {a["id"] for a in data["agenten"]} >= {"jarvis", "gremium", "recherche", "echtzeit"}
    assert {w["id"] for w in data["ablaeufe"]} >= {"coach", "fristwarnung", "termin_vorbereitung"}
    assert data["ablaeufe_schleife"] in {"läuft", "aus"}
    windy = next(i for i in data["integrationen"] if i["id"] == "windy")
    assert windy["status"] == "bereit"
    assert "geheimer-test-schluessel" not in json.dumps(data)  # nie Schlüssel ausliefern
    hubspot = next(i for i in data["integrationen"] if i["id"] == "hubspot")
    assert hubspot["status"] == "nicht_eingerichtet" and hubspot["hinweis"]


def test_n8n_nicht_eingerichtet_und_eingerichtet():
    path = config.data_dir() / "n8n.json"
    path.unlink(missing_ok=True)
    assert uebersicht.n8n_workflows()["status"] == "nicht_eingerichtet"
    path.write_text(json.dumps({"workflows": {"rechnung": {"url": "https://n8n.example/webhook/x", "freigabe": False,
                                                           "beschreibung": "Rechnung erstellen"}}}), encoding="utf-8")
    try:
        out = uebersicht.n8n_workflows()
        assert out["status"] == "bereit" and out["workflows"] == [
            {"name": "rechnung", "beschreibung": "Rechnung erstellen", "freigabe": False}]
        assert "n8n.example" not in json.dumps(out)  # Webhook-Adressen bleiben im Backend
    finally:
        path.unlink()


def test_bridge_route_registriert():
    import inspect

    from jarvis import v3_bridge

    assert '"/internal/automations"' in inspect.getsource(v3_bridge)


def test_sprachangaben_folgen_der_echten_stimme(monkeypatch):
    """Kopfzeile des Command Centers: ohne gültigen ElevenLabs-Schlüssel spricht die kostenlose Stimme – so wird es auch angezeigt."""
    from jarvis import tts, uebersicht
    monkeypatch.setattr(tts, "current_voice", lambda: "eleven:JBFqnCBsd6RMkjVDRZzb")
    monkeypatch.setattr(tts, "has_eleven", lambda: True)
    monkeypatch.setattr(tts, "eleven_problem", None)
    facts = uebersicht.speech_facts()
    assert facts["stimme"] == "George" and facts["stimme_anbieter"] == "ElevenLabs"
    assert facts["erkennung"].startswith("Whisper ") and facts["erkennung"].endswith("· lokal")
    monkeypatch.setattr(tts, "has_eleven", lambda: False)
    facts = uebersicht.speech_facts()
    assert facts["stimme_anbieter"] == "Microsoft (edge-tts)" and facts["stimme"] == "Conrad"
