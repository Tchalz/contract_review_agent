"""
Extracts plain text from an uploaded contract file (PDF, DOCX, or TXT) so
the rest of the pipeline only ever deals with plain strings.

Also tracks page boundaries for PDFs, so callers can look up which page a
given character offset (e.g. a flagged clause's snippet position) falls
on, and cite it in the report ("Page 4") rather than just showing a
floating snippet with no location.
"""

import bisect
from pathlib import Path


def extract_text(file_path: str) -> str:
    """
    Extracts text from a contract file based on its extension.

    Kept for backward compatibility / simple callers that don't need page
    citations. See extract_text_with_pages for page-aware extraction.

    Args:
        file_path: Path to a .pdf, .docx, or .txt file.

    Returns:
        The extracted plain text.
    """
    text, _ = extract_text_with_pages(file_path)
    return text


def extract_text_with_pages(file_path: str) -> tuple[str, list[int]]:
    """
    Extracts text from a contract file, along with a list of character
    offsets marking where each page begins in the returned text.

    PDFs only get real page boundaries (pdfplumber gives us one page at a
    time). DOCX and TXT have no reliable page concept without a renderer,
    so their offset list is empty — callers should treat an empty list as
    "no page information available" rather than "single page".

    Args:
        file_path: Path to a .pdf, .docx, or .txt file.

    Returns:
        (full_text, page_starts) where page_starts[i] is the character
        offset at which page i+1 begins in full_text. Look up a page
        number for a given offset with page_number_at().

    Raises:
        ValueError: If the file extension isn't supported.
    """
    ext = Path(file_path).suffix.lower()

    if ext == ".pdf":
        import pdfplumber
        text_parts = []
        page_starts = []
        cursor = 0
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if not page_text:
                    continue
                page_starts.append(cursor)
                text_parts.append(page_text)
                cursor += len(page_text) + 1  # +1 accounts for the "\n" joiner below
        return "\n".join(text_parts), page_starts

    if ext == ".docx":
        import docx
        d = docx.Document(file_path)
        text = "\n".join(p.text for p in d.paragraphs if p.text)
        return text, []

    if ext == ".txt":
        text = Path(file_path).read_text(encoding="utf-8", errors="ignore")
        return text, []

    raise ValueError(f"Unsupported file type: {ext}. Use .pdf, .docx, or .txt.")


def page_number_at(page_starts: list[int], offset: int):
    """
    Looks up the 1-indexed page number a character offset falls on.

    Args:
        page_starts: The page_starts list from extract_text_with_pages.
        offset: A character offset into the corresponding full_text.

    Returns:
        The 1-indexed page number, or None if page_starts is empty (i.e.
        the source had no page information — DOCX/TXT, or a PDF with no
        extractable pages).
    """
    if not page_starts:
        return None
    return bisect.bisect_right(page_starts, offset)
