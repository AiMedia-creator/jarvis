"""Ägyptisches Arabisch: Einstellung, Erkennung, Prompt, Stimme und Echtzeit."""

import pytest

from jarvis import config, sprache, tts


@pytest.fixture
def stelle(monkeypatch):
    def setzen(wert):
        if wert is None:
            monkeypatch.delenv("JARVIS_SPRACHE", raising=False)
        else:
            monkeypatch.setenv("JARVIS_SPRACHE", wert)
    return setzen


def test_standard_bleibt_deutsch(stelle):
    stelle(None)
    assert sprache.aktuell() == "de" and sprache.whisper_sprache() == "de" and sprache.prompt_satz() == ""
    assert sprache.whisper_modell("base") == "base" and sprache.echtzeit_sprache() == "de"


def test_aegyptisch_steuert_erkennung_prompt_und_echtzeit(stelle):
    stelle("arz")
    assert sprache.whisper_sprache() == "ar" and sprache.whisper_modell("base") == "small"
    assert "إزيك" in sprache.whisper_vorgabe("Jarvis, HubSpot")
    satz = sprache.prompt_satz()
    assert "Ägyptisch" in satz and "nicht Hocharabisch" in satz and "يا ماستر" in satz and "immer" in satz
    assert sprache.echtzeit_sprache() == "ar" and "أهلاً يا ماستر" in sprache.begruessung("Guten Tag, Master.")


def test_automatisch_laesst_whisper_erkennen_und_antwortet_in_der_sprache_des_nutzers(stelle):
    stelle("auto")
    assert sprache.whisper_sprache() is None
    assert "Sprache der letzten Nutzernachricht" in sprache.prompt_satz()
    assert sprache.echtzeit_sprache() == "de" and sprache.begruessung("Hallo") == "Hallo"


def test_unbekannter_wert_faellt_auf_deutsch_zurueck(stelle):
    stelle("klingonisch")
    assert sprache.aktuell() == "de"


@pytest.mark.parametrize("text,arabisch", [
    ("إزيك يا ماستر، عندك ميتنج مع Weber الساعة 3", True),
    ("Guten Tag, Master. Du hast drei Termine.", False),
    ("OK", False), ("", False), ("123 456", False),
])
def test_erkennt_arabische_schrift_auch_mit_namen_in_lateinischer_schrift(text, arabisch):
    assert sprache.ist_arabisch(text) is arabisch


def test_arabischer_satz_bekommt_aegyptische_stimme_und_keine_deutsche_aussprache(stelle, monkeypatch):
    stelle("auto")
    monkeypatch.delenv("JARVIS_STIMME_ARABISCH", raising=False)
    assert sprache.stimme_fuer("تمام كده يا ماستر", "eleven:abc") == "edge:ar-EG-ShakirNeural"
    assert sprache.stimme_fuer("Alles klar.", "eleven:abc") == "eleven:abc"
    assert sprache.fuer_sprache("**مهم**: شوف https://x.de/a") == "مهم: شوف الرابط"
    gesendet = []
    monkeypatch.setattr(tts, "_edge", lambda text, voice: gesendet.append((text, voice)) or b"mp3")
    monkeypatch.setattr(tts, "_audio_cache", None)
    tts.synthesize("الاجتماع الساعة 14:30 يا ماستر", "edge:de-DE-ConradNeural")
    assert gesendet == [("الاجتماع الساعة 14:30 يا ماستر", "ar-EG-ShakirNeural")]  # 14:30 nicht als „14 Uhr 30“
    tts.synthesize("Treffen um 14:30.", "edge:de-DE-ConradNeural")
    assert gesendet[-1] == ("Treffen um 14 Uhr 30.", "de-DE-ConradNeural")


def test_einstellung_speichern_und_prompts_neu_laden(stelle, monkeypatch, tmp_path):
    from jarvis import echtzeit
    stelle(None)
    gespeichert = {}
    monkeypatch.setattr(config, "save", lambda k, v: gespeichert.__setitem__(k, v))
    monkeypatch.setattr(echtzeit, "status", lambda model_id=None: {"ok": True})
    neu = []
    echtzeit.save_settings({"sprache": "arz"}, None, lambda: neu.append(1))
    assert gespeichert == {"sprache": "arz"} and neu == [1]
    echtzeit.save_settings({"sprache": "de"}, None, lambda: neu.append(1))
    assert gespeichert["sprache"] is None
    with pytest.raises(ValueError):
        echtzeit.save_settings({"sprache": "fr"})


def test_systemprompt_enthaelt_die_sprache(stelle):
    from jarvis import agent as A
    stelle("arz")
    a = A.Agent(channel="web", conversation_id="sprache-test")
    assert "Ägyptisch" in a.system and "يا ماستر" in a.system


def test_echtzeit_agent_spricht_arabisch(stelle, monkeypatch):
    from jarvis import eleven_agent
    stelle("arz")
    monkeypatch.setattr(eleven_agent, "_system_prompt", lambda: "PROMPT")
    monkeypatch.setattr(eleven_agent, "_voice_id", lambda: "v")
    conf = eleven_agent.conversation_config([], 1.0)
    assert conf["agent"]["language"] == "ar" and conf["agent"]["first_message"].startswith("أهلاً")


def test_automatisch_waehlt_nur_zwischen_deutsch_und_arabisch():
    assert sprache.waehle([("nl", 0.6), ("de", 0.3), ("ar", 0.05)]) == "de"
    assert sprache.waehle([("en", 0.5), ("ar", 0.4), ("de", 0.1)]) == "ar"
    assert sprache.waehle([]) == "de"
