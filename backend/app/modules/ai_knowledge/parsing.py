from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

from app.core.errors import AppError


@dataclass(frozen=True)
class ParsedSection:
    text: str
    page_number: int | None = None
    heading: str | None = None


@dataclass(frozen=True)
class ParsedDocument:
    sections: tuple[ParsedSection, ...]


@dataclass(frozen=True)
class TextChunk:
    index: int
    content: str
    page_number: int | None
    heading: str | None


class DocumentParseError(AppError):
    def __init__(self, code: str = "DOCUMENT_PARSE_FAILED") -> None:
        super().__init__(status_code=422, code=code, message="文档内容无法解析")


def sniff_format(data: bytes, filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if "word/document.xml" in archive.namelist():
                    return "docx"
        except zipfile.BadZipFile as exc:
            raise DocumentParseError() from exc
    if suffix in {".txt", ".md"}:
        return suffix[1:]
    raise DocumentParseError("DOCUMENT_FORMAT_UNSUPPORTED")


def _clean(text: str) -> str:
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def parse_document(data: bytes, filename: str) -> ParsedDocument:
    kind = sniff_format(data, filename)
    try:
        if kind in {"txt", "md"}:
            return ParsedDocument((ParsedSection(_clean(data.decode("utf-8-sig"))),))
        if kind == "docx":
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                root = ElementTree.fromstring(archive.read("word/document.xml"))
            paragraphs = ["".join(node.itertext()) for node in root.iter() if node.tag.endswith("}p")]
            return ParsedDocument((ParsedSection(_clean("\n".join(paragraphs))),))
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return ParsedDocument(tuple(ParsedSection(_clean(page.extract_text() or ""), i + 1) for i, page in enumerate(reader.pages)))
    except (UnicodeError, ValueError, OSError, zipfile.BadZipFile) as exc:
        raise DocumentParseError() from exc


def chunk_document(document: ParsedDocument, size: int = 500, overlap: int = 80) -> tuple[TextChunk, ...]:
    if size <= 0 or overlap < 0 or overlap >= size:
        raise ValueError("invalid chunk settings")
    result: list[TextChunk] = []
    for section in document.sections:
        start = 0
        while start < len(section.text):
            stop = min(start + size, len(section.text))
            if stop < len(section.text):
                boundary = max(section.text.rfind("\n", start, stop), section.text.rfind("。", start, stop))
                if boundary > start + size // 2:
                    stop = boundary + 1
            content = section.text[start:stop].strip()
            if content:
                result.append(TextChunk(len(result), content, section.page_number, section.heading))
            if stop >= len(section.text):
                break
            start = stop - overlap
    return tuple(result)
