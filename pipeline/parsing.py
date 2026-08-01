"""
Extracts plain text from an uploaded contract file (PDF, DOCX, or TXT) so
the rest of the pipeline only ever deals with plain strings.
"""

from pathlib import Path


def extract_text(file_path: str) -> str:
    """
    Extracts text from a contract file based on its extension.

    Args:
        file_path: Path to a .pdf, .docx, or .txt file.

    Returns:
        The extracted plain text.

    Raises:
        ValueError: If the file extension isn't supported.
    """
    ext = Path(file_path).suffix.lower()

    if ext == ".pdf":
        import pdfplumber
        text_parts = []
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
        return "\n".join(text_parts)

    if ext == ".docx":
        import docx
        d = docx.Document(file_path)
        return "\n".join(p.text for p in d.paragraphs if p.text)

    if ext == ".txt":
        return Path(file_path).read_text(encoding="utf-8", errors="ignore")

    raise ValueError(f"Unsupported file type: {ext}. Use .pdf, .docx, or .txt.")
