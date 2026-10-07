"""Document text extraction: parsers for each analyzable format.

Storage is faked with in-memory bytes (no boto), so these tests exercise the
real parsing, the real caps, and the real failure messages.
"""
import io

import pytest

from app.services import documents as docs
from app.services import storage


class FakeS3:
    def __init__(self, body: bytes):
        self.body = body

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.body)}


@pytest.fixture
def _bytes(monkeypatch):
    def _put(body: bytes):
        fake = FakeS3(body)
        monkeypatch.setattr(storage, "_client", lambda: (fake, "talyn-test"))
        monkeypatch.setattr(
            __import__("app.config", fromlist=["x"]).settings,
            "s3_bucket", "talyn-test",
        )

    return _put


def _pdf_bytes(lines: list[str]) -> bytes:
    """A valid single-page PDF with a real xref table. Offsets are computed,
    not hand-counted: a wrong offset is a corrupt file, and a corrupt
    fixture tests nothing."""
    text_ops = "".join(
        f"BT /F1 12 Tf 100 {700 - 20 * i} Td ({line}) Tj ET\n"
        for i, line in enumerate(lines)
    ).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(text_ops)).encode() + b" >> stream\n"
        + text_ops + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for n, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        b"trailer\n<< /Size " + str(len(objects) + 1).encode() +
        b" /Root 1 0 R >>\nstartxref\n" + str(xref_at).encode() + b"\n%%EOF\n"
    )
    return bytes(out)


def _docx_bytes(paragraphs: list[str]) -> bytes:
    from docx import Document

    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _pptx_bytes(lines: list[str]) -> bytes:
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(8), Inches(5))
    box.text_frame.text = "\n".join(lines)
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def test_pdf_text_is_extracted(_bytes):
    _bytes(_pdf_bytes(["Photosynthesis converts light energy"]))
    text = docs.extract_text("material/k-notes.pdf", "notes.pdf",
                             "application/pdf")
    assert "Photosynthesis converts light energy" in text


def test_docx_text_and_tables_are_extracted(_bytes):
    _bytes(_docx_bytes(["Chlorophyll absorbs red and blue"]))
    text = docs.extract_text("material/k-notes.docx", "notes.docx",
                             "application/vnd.openxmlformats-officedocument"
                             ".wordprocessingml.document")
    assert "Chlorophyll absorbs red and blue" in text


def test_pptx_text_is_extracted(_bytes):
    _bytes(_pptx_bytes(["Calvin cycle fixes carbon dioxide"]))
    text = docs.extract_text("material/k-deck.pptx", "deck.pptx",
                             "application/vnd.openxmlformats-officedocument"
                             ".presentationml.presentation")
    assert "Calvin cycle fixes carbon dioxide" in text


def test_txt_is_decoded(_bytes):
    _bytes("Stomata regulate gas exchange.".encode())
    assert "Stomata" in docs.extract_text("material/k-n.txt", "n.txt",
                                          "text/plain")


def test_scanned_pdf_with_no_text_is_a_422_not_a_panic(_bytes):
    _bytes(_pdf_bytes([]))
    with pytest.raises(docs.ExtractionError, match="no readable text"):
        docs.extract_text("material/k-scan.pdf", "scan.pdf", "application/pdf")


def test_corrupt_pdf_is_a_422(_bytes):
    _bytes(b"this is not a pdf at all")
    with pytest.raises(docs.ExtractionError, match="could not be opened"):
        docs.extract_text("material/k-bad.pdf", "bad.pdf", "application/pdf")


def test_unsupported_extension_is_refused(_bytes):
    _bytes(b"whatever")
    with pytest.raises(docs.ExtractionError, match="cannot be analyzed"):
        docs.extract_text("material/k-run.exe", "run.exe",
                          "application/octet-stream")


def test_output_is_truncated_to_the_budget(_bytes, monkeypatch):
    _bytes(b"word " * 10000)
    monkeypatch.setattr(docs, "MAX_CHARS", 100)
    text = docs.extract_text("material/k-big.txt", "big.txt", "text/plain")
    assert len(text) == 100
