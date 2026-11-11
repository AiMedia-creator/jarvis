"""Dateianhänge: Typprüfung, Extraktion, Grenzen, nur Arbeitsspeicher, ehrliche Hinweise an das Modell."""

import io
import zipfile

import pytest

from jarvis import anhaenge, modelle
from helpers import B, Stream, make_agent


@pytest.fixture(autouse=True)
def _leer():
    anhaenge.clear()
    yield
    anhaenge.clear()


def _docx(text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml",
                   '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
                   f'<w:p><w:r><w:t>{text}</w:t></w:r></w:p><w:p><w:r><w:t>Zweiter Absatz</w:t></w:r></w:p></w:body></w:document>')
    return buf.getvalue()


def _xlsx() -> bytes:
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rel = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook {ns} {rel}><sheets><sheet name="Umsatz" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="x"/></Relationships>')
        z.writestr("xl/sharedStrings.xml", f'<sst {ns}><si><t>Kunde</t></si><si><t>Weber</t></si></sst>')
        z.writestr("xl/worksheets/sheet1.xml",
                   f'<worksheet {ns}><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="inlineStr"><is><t>Betrag</t></is></c></row>'
                   '<row r="2"><c r="A2" t="s"><v>1</v></c><c r="B2"><v>1200</v></c></row></sheetData></worksheet>')
    return buf.getvalue()


def _pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = io.BytesIO(), []
    out.write(b"%PDF-1.4\n")
    for i, obj in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i + obj + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(b"trailer << /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, xref))
    return out.getvalue()


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def test_text_csv_docx_xlsx_pdf_werden_extrahiert():
    assert anhaenge.add("raum1", "notiz.txt", "Hallo Welt".encode())["art"] == "Text"
    assert anhaenge.add("raum1", "zahlen.csv", b"a;b\n1;2")["typ"] == "csv"
    doc = anhaenge.add("raum1", "brief.docx", _docx("Angebot fuer Weber"))
    assert doc["art"] == "Word" and doc["detail"] == "2 Absätze"
    sheet = anhaenge.add("raum1", "umsatz.xlsx", _xlsx())
    assert sheet["art"] == "Excel"
    pdf = anhaenge.add("raum1", "vertrag.pdf", _pdf("Vertragslaufzeit zwoelf Monate " * 10))
    assert pdf["art"] == "PDF" and pdf["detail"] == "1 Seiten"
    taken = {a.name: a for a in anhaenge.take("raum1")}
    assert "Kunde;Betrag" in taken["umsatz.xlsx"].text and "Weber;1200" in taken["umsatz.xlsx"].text
    assert "Angebot fuer Weber\nZweiter Absatz" == taken["brief.docx"].text
    assert "Vertragslaufzeit" in taken["vertrag.pdf"].text and taken["vertrag.pdf"].data is None
    assert anhaenge.take("raum1") == []  # einmal übernommen = aus dem Zwischenspeicher entfernt


@pytest.mark.parametrize("name,data,text", [
    ("leer.txt", b"", "leer"),
    ("tool.exe", b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 50, "nicht unterstützt"),
    ("bild.pdf", PNG, "passen nicht zusammen"),            # Endung lügt
    ("skript.js", b"alert(1)", "nicht unterstützt"),
    ("kaputt.docx", b"PK\x03\x04kaputt", "beschädigt"),
])
def test_unzulaessiges_wird_verstaendlich_abgelehnt(name, data, text):
    with pytest.raises(anhaenge.AttachmentError, match=text):
        anhaenge.add("raum1", name, data)
    assert anhaenge.pending("raum1") == []


def test_grenzen_je_datei_und_nachricht(monkeypatch):
    with pytest.raises(anhaenge.AttachmentError, match="zu groß"):
        anhaenge.add("raum1", "gross.txt", b"a" * (anhaenge.MAX_FILE + 1))
    with pytest.raises(anhaenge.AttachmentError, match="Bild ist zu groß"):
        anhaenge.add("raum1", "foto.png", PNG + b"\x00" * anhaenge.MAX_IMAGE)
    for i in range(anhaenge.MAX_FILES):
        anhaenge.add("raum1", f"n{i}.txt", b"x")
    with pytest.raises(anhaenge.AttachmentError, match="Höchstens"):
        anhaenge.add("raum1", "zuviel.txt", b"x")
    long = anhaenge.add("raum2", "lang.txt", b"y" * (anhaenge.MAX_TEXT + 10))
    assert long["gekuerzt"] is True


def test_zip_bombe_wird_abgewiesen(monkeypatch):
    monkeypatch.setattr(anhaenge, "MAX_UNZIPPED", 1000)
    with pytest.raises(anhaenge.AttachmentError, match="zu groß"):
        anhaenge.add("raum1", "bombe.docx", _docx("A" * 5000))


def test_entfernen_nur_im_eigenen_gespraech():
    item = anhaenge.add("raum1", "a.txt", b"x")
    assert anhaenge.remove("anderer-raum", item["id"]) is False
    assert anhaenge.remove("raum1", "../../etc") is False
    assert anhaenge.remove("raum1", item["id"]) is True
    assert anhaenge.pending("raum1") == []


def test_bilder_nur_fuer_modelle_mit_bildverstaendnis():
    anhaenge.add("raum1", "foto.png", PNG)
    image = anhaenge.take("raum1")
    with_images = anhaenge.blocks(image, modelle.PROVIDERS["anthropic"])
    assert [b["type"] for b in with_images] == ["text", "image"]
    without = anhaenge.blocks(image, modelle.PROVIDERS["ollama"])
    assert [b["type"] for b in without] == ["text"] and "kann keine Bilder sehen" in without[0]["text"]


def test_anhang_landet_im_zug_aber_nicht_im_gedaechtnis_oder_checkpoint():
    from jarvis import graph

    agent = make_agent()
    seen = []

    def stream(**kw):
        seen.append(kw["messages"][-1]["content"])
        return Stream(B(stop_reason="end_turn", content=[B(type="text", text="Gelesen.")]), ["Gelesen."])

    agent.client = B(beta=B(messages=B(stream=stream)))
    anhaenge.add("raum1", "geheim.txt", "Vertraulicher Inhalt XYZ".encode())
    answer = agent.ask("Fasse die Datei zusammen", attachments=anhaenge.take("raum1"))
    assert answer == "Gelesen."
    content = seen[0]
    assert isinstance(content, list) and content[0]["text"].startswith(anhaenge.MARKER)
    assert "Vertraulicher Inhalt XYZ" in content[0]["text"] and "Fasse die Datei zusammen" in content[-1]["text"]
    # Checkpoint-Auszug ohne Dateiinhalt
    assert "Vertraulicher" not in graph._text_of(content)
    # dauerhaftes Gedächtnis: nur der getippte Text
    episodes = agent.memory_store.recent_episodes(agent.conversation_id, 5)
    assert episodes and all("Vertraulicher" not in e["user_text"] for e in episodes)


# --- Brücken-Endpunkte (echte aiohttp-App, ohne laufenden Jarvis) -----------------------------------------

TOKEN = "offline-unit-test-bridge-credential-only"
AUTH = {"Authorization": "Bearer " + TOKEN}


@pytest.mark.asyncio
async def test_bruecke_upload_liste_entfernen_und_grenzen(monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    from jarvis import v3_bridge

    client = TestClient(TestServer(v3_bridge.create_app(TOKEN, demo=False)))
    await client.start_server()
    try:
        url = "/internal/anhaenge?conversation_id=room-test1&name=notiz.txt"
        assert (await client.post(url, data=b"Hallo")).status == 401  # ohne Token
        r = await client.post(url, data="Meeting am Freitag".encode(), headers=AUTH)
        body = await r.json()
        assert r.status == 200 and body["anhang"]["art"] == "Text"
        listed = (await (await client.get("/internal/anhaenge?conversation_id=room-test1", headers=AUTH)).json())["anhaenge"]
        assert [a["name"] for a in listed] == ["notiz.txt"]
        r = await client.post("/internal/anhaenge?conversation_id=room-test1&name=x.exe", data=b"MZ\x00\x00" * 10, headers=AUTH)
        assert r.status == 415 and "nicht unterstützt" in (await r.json())["fehler"]
        r = await client.post("/internal/anhaenge?conversation_id=../x&name=a.txt", data=b"a", headers=AUTH)
        assert r.status == 400
        monkeypatch.setattr(anhaenge, "MAX_FILE", 1000)
        r = await client.post("/internal/anhaenge?conversation_id=room-test1&name=big.txt", data=b"a" * 2000, headers=AUTH)
        assert r.status == 413
        r = await client.delete(f"/internal/anhaenge/{body['anhang']['id']}?conversation_id=room-test1", headers=AUTH)
        assert r.status == 200 and anhaenge.pending("room-test1") == []
    finally:
        await client.close()
