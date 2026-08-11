import docx
import io


def parse_docx(file_bytes: bytes) -> str:
    """
    Extracts all text from a Word document (.docx).
    Returns all paragraphs joined as plain text.
    """
    document = docx.Document(io.BytesIO(file_bytes))
    paragraphs = [p.text.strip() for p in document.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs)