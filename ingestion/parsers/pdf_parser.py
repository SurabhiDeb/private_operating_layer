import pdfplumber
import io


def parse_pdf(file_bytes: bytes) -> str:
    """
    Extracts all text from a PDF file.
    file_bytes is the raw bytes of the uploaded PDF.
    Returns a single string of all text across all pages.
    """
    text_parts = []

    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text.strip())

    return "\n\n".join(text_parts)