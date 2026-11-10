from jarvis import audit


def test_kuerzt_und_entfernt_geheimnisse():
    audit.record("test", "mail_senden", {"an": "a@b.de", "text": "x" * 1000, "api_token": "geheim",
                                         "bestaetigt_vom_nutzer": True}, "ok", "gesendet")
    e = audit.tail(1)[0]
    assert e["extern"] is True and e["freigabe"] is True
    assert "api_token" not in e["argumente"] and len(e["argumente"]["text"]) < 300
    assert oct(audit.path().stat().st_mode)[-3:] == "600"


def test_status_erkennung():
    assert audit.status_from_text("Versand abgelehnt: ...", True) == "freigabe_fehlt"
    assert audit.status_from_text("Fehler", True) == "fehler"
    assert audit.status_from_text("ok", False) == "ok"
