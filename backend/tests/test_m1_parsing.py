import io
import zipfile

import pytest

from app.modules.ai_knowledge.parsing import DocumentParseError, chunk_document, parse_document, sniff_format


def test_text_parsing_and_deterministic_overlap():
    parsed = parse_document(("段落。" * 300).encode(), "guide.md")
    first = chunk_document(parsed)
    assert first == chunk_document(parsed)
    assert len(first) > 1 and all(len(chunk.content) <= 500 for chunk in first)


def test_docx_is_sniffed_from_content():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", '<w:document xmlns:w="x"><w:p><w:r><w:t>校园指南</w:t></w:r></w:p></w:document>')
    assert sniff_format(buffer.getvalue(), "renamed.bin") == "docx"
    assert parse_document(buffer.getvalue(), "renamed.bin").sections[0].text == "校园指南"


def test_unsupported_and_invalid_chunk_settings_are_rejected():
    with pytest.raises(DocumentParseError) as exc:
        parse_document(b"binary", "file.exe")
    assert exc.value.code == "DOCUMENT_FORMAT_UNSUPPORTED"
    with pytest.raises(ValueError):
        chunk_document(parse_document(b"text", "a.txt"), size=80, overlap=80)
