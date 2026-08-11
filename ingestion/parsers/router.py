from ingestion.parsers.pdf_parser import parse_pdf
from ingestion.parsers.excel_parser import parse_excel
from ingestion.parsers.docx_parser import parse_docx


SUPPORTED_TYPES = {
    "application/pdf": parse_pdf,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": parse_excel,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": parse_docx,
}

EXTENSION_MAP = {
    ".pdf": "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def parse_document(file_bytes: bytes, filename: str) -> str:
    """
    Detects file type from the filename extension and routes to the correct parser.
    Returns extracted plain text ready to be passed into the ingestion pipeline.
    Raises ValueError if the file type is not supported.
    """
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    content_type = EXTENSION_MAP.get(ext)

    if not content_type or content_type not in SUPPORTED_TYPES:
        raise ValueError(f"Unsupported file type: '{ext}'. Supported types: .pdf, .xlsx, .docx")

    parser_fn = SUPPORTED_TYPES[content_type]
    return parser_fn(file_bytes)