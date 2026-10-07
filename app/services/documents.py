"""Text extraction from library documents for AI analysis.

The coach reasons over text, not file formats, so someone has to turn the
bytes in R2 into characters first. That someone is this module: it downloads
a capped prefix of the object and parses PDFs and Word documents into plain
text, truncated to an analysis budget.

Two caps, both load-bearing on a 512 MB container:

- DOWNLOAD_BYTES bounds what comes over the wire and into memory. Parsing
  streams from these bytes, never from the whole object.
- MAX_CHARS bounds what is sent to the coach. Input tokens cost money on
  every analysis, and a 100 MB document is not 100 MB of insight.

Only the owner's claimed library files reach here (the analyze endpoint
loads the row scoped to the learner), so extraction never touches another
learner's bytes.
"""
import io

from app.services import storage
from app.services.storage import StorageError

# Largest object prefix pulled for parsing. Above this, download_bytes would
# hold more in memory than the container should spare.
DOWNLOAD_BYTES = 8 * 1024 * 1024
# Characters handed to the coach. ~30k tokens of input: enough for a serious
# document, small enough that one analysis cannot cost real money.
MAX_CHARS = 120_000
# Pages parsed before stopping. A 400-page textbook contributes its opening
# chapters, which is where the topics and objectives live anyway.
MAX_PAGES = 60


class ExtractionError(Exception):
    """The file could not be turned into text; message is safe to show."""


def extract_text(storage_key: str, filename: str, content_type: str) -> str:
    """Download, parse, and truncate. Raises ExtractionError or StorageError."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    try:
        data = storage.download_bytes(storage_key, DOWNLOAD_BYTES)
    except StorageError as e:
        raise ExtractionError(
            "Could not read that file from storage. Please re-upload it."
        ) from e

    if ext == "pdf":
        text = _from_pdf(data)
    elif ext == "docx":
        text = _from_word(data)
    elif ext == "pptx":
        text = _from_slides(data)
    elif ext == "txt":
        text = _from_text(data)
    else:
        # Content types were allowlisted at presign, so this is unreachable
        # through the API — but a direct service call should not parse
        # mystery bytes as if they were a document.
        raise ExtractionError(
            f"Files ending in .{ext or '(no extension)'} cannot be analyzed. "
            "Upload a PDF, Word, PowerPoint, or text file."
        )

    text = " ".join(text.split())
    if not text:
        raise ExtractionError(
            "That file has no readable text — it may be scanned images. "
            "Upload a document with selectable text."
        )
    return text[:MAX_CHARS]


def _from_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as e:  # pragma: no cover - dependency is installed
        raise ExtractionError("PDF analysis is unavailable.") from e
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as e:
        raise ExtractionError(
            "That PDF could not be opened. It may be corrupted or password "
            "protected."
        ) from e
    parts = []
    for page in reader.pages[:MAX_PAGES]:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            # One bad page must not fail the whole document.
            continue
    return "\n".join(parts)


def _from_word(data: bytes) -> str:
    try:
        from docx import Document
    except ImportError as e:  # pragma: no cover - dependency is installed
        raise ExtractionError("Word analysis is unavailable.") from e
    try:
        doc = Document(io.BytesIO(data))
    except Exception as e:
        raise ExtractionError(
            "That Word file could not be opened. It may be corrupted."
        ) from e
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            parts.extend(cell.text for cell in row.cells)
    return "\n".join(parts)


def _from_slides(data: bytes) -> str:
    try:
        from pptx import Presentation
    except ImportError as e:  # pragma: no cover - dependency is installed
        raise ExtractionError("Slide analysis is unavailable.") from e
    try:
        deck = Presentation(io.BytesIO(data))
    except Exception as e:
        raise ExtractionError(
            "That deck could not be opened. It may be corrupted."
        ) from e
    parts = []
    for slide in deck.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                parts.append(shape.text)
            if shape.has_table:
                for row in shape.table.rows:
                    parts.extend(cell.text for cell in row.cells)
    return "\n".join(parts)


def _from_text(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")
