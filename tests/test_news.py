import pytest

from jarvis.tools import ToolFailure, news

RSS = b"""<?xml version='1.0'?><rss><channel><item><title>Test &amp; Meldung</title><description>&lt;p&gt;Text&lt;/p&gt;</description>
<pubDate>Sat, 17 Oct 2026 08:00:00 +0200</pubDate><link>http://a/1</link></item></channel></rss>"""
ATOM = b"""<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'><entry><title>Atom Meldung</title>
<summary>Kurz</summary><updated>2026-10-17T09:00:00Z</updated><link href='http://b/2'/></entry></feed>"""


def test_rss_atom_und_ausfall(mock_server, monkeypatch):
    url, _ = mock_server(lambda m, p, b: (200, RSS if p == "/rss" else ATOM))
    monkeypatch.setitem(news.FEEDS, "test", [("RSS", url + "/rss"), ("Atom", url + "/atom"), ("Kaputt", "http://127.0.0.1:1/x")])
    out = news.nachrichten("test")
    assert out.index("Atom Meldung") < out.index("Test & Meldung")  # neueste zuerst
    assert "Atom Meldung" in news.nachrichten("test", stichwort="atom")
    assert "Test & Meldung" not in news.nachrichten("test", stichwort="atom")


def test_unbekannte_rubrik():
    with pytest.raises(ToolFailure):
        news.nachrichten("gibtsnicht")
