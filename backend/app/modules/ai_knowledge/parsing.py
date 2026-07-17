from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

from app.core.errors import AppError


MAX_DOCX_ENTRIES = 1_000
MAX_DOCX_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
MAX_DOCX_ENTRY_BYTES = 32 * 1024 * 1024
MAX_DOCX_COMPRESSION_RATIO = 200


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


def _safe_docx_archive(data: bytes) -> zipfile.ZipFile:
    """Open a DOCX after applying bounded, non-extracting ZIP checks.

    We never extract archive paths.  Limits are checked from the central directory
    before reading XML so a highly compressed document cannot exhaust memory.
    """

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        entries = archive.infolist()
    except (OSError, zipfile.BadZipFile) as exc:
        raise DocumentParseError("DOCUMENT_FORMAT_UNSUPPORTED") from exc
    try:
        if not entries or len(entries) > MAX_DOCX_ENTRIES:
            raise DocumentParseError("DOCUMENT_ARCHIVE_UNSAFE")
        total = 0
        for entry in entries:
            name = PurePosixPath(entry.filename.replace("\\", "/"))
            if name.is_absolute() or ".." in name.parts:
                raise DocumentParseError("DOCUMENT_ARCHIVE_UNSAFE")
            total += entry.file_size
            if entry.file_size > MAX_DOCX_ENTRY_BYTES or total > MAX_DOCX_UNCOMPRESSED_BYTES:
                raise DocumentParseError("DOCUMENT_ARCHIVE_UNSAFE")
            if entry.file_size > 1024 * 1024:
                compressed = max(entry.compress_size, 1)
                if entry.file_size / compressed > MAX_DOCX_COMPRESSION_RATIO:
                    raise DocumentParseError("DOCUMENT_ARCHIVE_UNSAFE")
        if "word/document.xml" not in archive.namelist():
            raise DocumentParseError("DOCUMENT_FORMAT_UNSUPPORTED")
        return archive
    except BaseException:
        archive.close()
        raise


def sniff_format(data: bytes, filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"PK\x03\x04"):
        archive = _safe_docx_archive(data)
        archive.close()
        return "docx"
    if suffix in {".txt", ".md"}:
        if b"\x00" in data[:8192]:
            raise DocumentParseError("DOCUMENT_FORMAT_UNSUPPORTED")
        return suffix[1:]
    raise DocumentParseError("DOCUMENT_FORMAT_UNSUPPORTED")


def _clean(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(char for char in text if char in "\n\t" or not unicodedata.category(char).startswith("C"))
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise DocumentParseError("DOCUMENT_TEXT_ENCODING_UNSUPPORTED")


def _require_text(sections: tuple[ParsedSection, ...]) -> ParsedDocument:
    useful = tuple(section for section in sections if section.text.strip())
    if not useful:
        raise DocumentParseError("DOCUMENT_NO_EXTRACTABLE_TEXT")
    return ParsedDocument(useful)


def parse_document(data: bytes, filename: str) -> ParsedDocument:
    kind = sniff_format(data, filename)
    try:
        if kind in {"txt", "md"}:
            return _require_text((ParsedSection(_clean(_decode_text(data))),))
        if kind == "docx":
            with _safe_docx_archive(data) as archive:
                xml = archive.read("word/document.xml")
            if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
                raise DocumentParseError("DOCUMENT_ARCHIVE_UNSAFE")
            root = ElementTree.fromstring(xml)
            paragraphs = [
                "".join(node.itertext())
                for node in root.iter()
                if node.tag.endswith("}p")
            ]
            return _require_text((ParsedSection(_clean("\n".join(paragraphs))),))

        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise DocumentParseError("DOCUMENT_PDF_ENCRYPTED")
        sections = tuple(
            ParsedSection(_clean(page.extract_text() or ""), i + 1)
            for i, page in enumerate(reader.pages)
        )
        return _require_text(sections)
    except DocumentParseError:
        raise
    except (UnicodeError, ValueError, OSError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
        raise DocumentParseError() from exc


def chunk_document(
    document: ParsedDocument,
    size: int = 500,
    overlap: int = 80,
) -> tuple[TextChunk, ...]:
    if size <= 0 or overlap < 0 or overlap >= size:
        raise ValueError("invalid chunk settings")
    result: list[TextChunk] = []
    for section in document.sections:
        start = 0
        while start < len(section.text):
            stop = min(start + size, len(section.text))
            if stop < len(section.text):
                boundary = max(
                    section.text.rfind("\n", start, stop),
                    section.text.rfind("。", start, stop),
                )
                if boundary > start + size // 2:
                    stop = boundary + 1
            content = section.text[start:stop].strip()
            if content:
                result.append(
                    TextChunk(
                        len(result),
                        content,
                        section.page_number,
                        section.heading,
                    )
                )
            if stop >= len(section.text):
                break
            start = stop - overlap
    return tuple(result)
