from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from app.core.errors import AppError
from app.modules.ai_knowledge.parsing import DocumentParseError, sniff_format


ALLOWED_MIME_TYPES: dict[str, frozenset[str]] = {
    "txt": frozenset({"text/plain"}),
    "md": frozenset({"text/markdown", "text/plain"}),
    "docx": frozenset(
        {
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }
    ),
    "pdf": frozenset({"application/pdf"}),
}
CANONICAL_MIME_TYPES = {
    "txt": "text/plain",
    "md": "text/markdown",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}


class KnowledgeArtifactInvalid(AppError):
    def __init__(
        self,
        code: str = "DOCUMENT_FORMAT_UNSUPPORTED",
        status: int = 415,
    ) -> None:
        super().__init__(status_code=status, code=code, message="知识文件无效")


@dataclass(frozen=True)
class StoredArtifact:
    object_key: str
    sha256: str
    size_bytes: int
    format: str
    mime_type: str


class KnowledgeArtifactStore:
    def __init__(self, root: Path, max_bytes: int = 20 * 1024 * 1024) -> None:
        self.root = root
        self.max = max_bytes

    async def save(
        self,
        stream,
        filename: str,
        *,
        content_type: str | None = None,
        object_key: str | None = None,
    ) -> StoredArtifact:
        ext = Path(filename).suffix.lower().lstrip(".")
        if ext not in ALLOWED_MIME_TYPES:
            raise KnowledgeArtifactInvalid()
        normalized_type = (content_type or "").split(";", 1)[0].strip().lower()
        if normalized_type and normalized_type not in ALLOWED_MIME_TYPES[ext]:
            raise KnowledgeArtifactInvalid()

        self.root.mkdir(parents=True, exist_ok=True)
        key = object_key or f"quarantine/{uuid4().hex[:2]}/{uuid4().hex}.{ext}"
        target = self._path(key)
        if target.exists():
            raise KnowledgeArtifactInvalid("ARTIFACT_ALREADY_EXISTS", 409)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        digest = hashlib.sha256()
        size = 0
        try:
            with temp.open("xb") as output:
                while chunk := await stream.read(1024 * 1024):
                    size += len(chunk)
                    if size > self.max:
                        raise KnowledgeArtifactInvalid("PAYLOAD_TOO_LARGE", 413)
                    digest.update(chunk)
                    output.write(chunk)
            if size == 0:
                raise KnowledgeArtifactInvalid("DOCUMENT_EMPTY", 422)

            # Validate the actual payload after the bounded streaming write.  DOCX
            # inspection reads only archive metadata and the parser enforces the
            # same limits again before XML is read.
            data = temp.read_bytes()
            try:
                detected = sniff_format(data, filename)
            except DocumentParseError as exc:
                raise KnowledgeArtifactInvalid(exc.code, 415) from exc
            if detected != ext:
                raise KnowledgeArtifactInvalid()
            if detected in {"txt", "md"}:
                try:
                    text = data.decode("utf-8-sig")
                except UnicodeDecodeError:
                    try:
                        text = data.decode("gb18030")
                    except UnicodeDecodeError as exc:
                        raise KnowledgeArtifactInvalid(
                            "DOCUMENT_TEXT_ENCODING_UNSUPPORTED",
                            415,
                        ) from exc
                if not text.strip():
                    raise KnowledgeArtifactInvalid("DOCUMENT_EMPTY", 422)
            os.replace(temp, target)
        except BaseException:
            temp.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
            raise
        return StoredArtifact(
            key,
            digest.hexdigest(),
            size,
            ext,
            CANONICAL_MIME_TYPES[ext],
        )

    def read(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise KnowledgeArtifactInvalid("ARTIFACT_NOT_FOUND", 404)
        return path.read_bytes()

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists() and not path.is_file():
            raise KnowledgeArtifactInvalid("INVALID_OBJECT_KEY", 422)
        path.unlink(missing_ok=True)
        # Remove empty generated directories, never walking above the store root.
        root = self.root.resolve()
        parent = path.parent
        while parent != root and parent.is_relative_to(root):
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent

    def _path(self, key: str) -> Path:
        raw = Path(key)
        root = self.root.resolve()
        candidate = (root / raw).resolve()
        if raw.is_absolute() or ".." in raw.parts or not candidate.is_relative_to(root):
            raise KnowledgeArtifactInvalid("INVALID_OBJECT_KEY", 422)
        # Reject symlinks at every existing level.  resolve() catches escaping
        # links; this additionally prevents aliases that remain inside root.
        current = root
        for part in raw.parts:
            current = current / part
            if current.exists() and current.is_symlink():
                raise KnowledgeArtifactInvalid("INVALID_OBJECT_KEY", 422)
        return candidate
