from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import uuid4

from fastapi import UploadFile

from docguard.settings import Settings


logger = logging.getLogger("docguard.uploads")


DEFAULT_MAX_UPLOAD_BYTES = 100 * 1024 * 1024
_CHUNK_SIZE = 1024 * 1024
_AGENT_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_DOCX_CONTENT_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/octet-stream",
}


class UploadValidationError(ValueError):
    pass


class UploadTooLargeError(UploadValidationError):
    pass


@dataclass(frozen=True)
class StoredUpload:
    upload_id: str
    agent_id: str
    filename: str
    size_bytes: int
    sha256: str
    agent_path: str

    @property
    def source_uri(self) -> str:
        return f"file://{self.agent_path}"


class UploadStorage:
    """Stores DOCX uploads outside OpenClaw state and returns an agent-visible Linux path."""

    def __init__(
        self,
        write_root: Path | str,
        agent_root: PurePosixPath | str,
        max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
    ) -> None:
        if max_upload_bytes <= 0:
            raise ValueError("max_upload_bytes must be positive")
        self.write_root = Path(write_root)
        self.agent_root = PurePosixPath(agent_root)
        self.max_upload_bytes = max_upload_bytes

    @classmethod
    def from_environment(cls) -> UploadStorage:
        settings = Settings.from_environment()
        return cls(settings.upload_write_root, settings.upload_agent_root, settings.upload_max_bytes)

    async def store_docx(self, agent_id: str, upload: UploadFile) -> StoredUpload:
        self._validate_agent_id(agent_id)
        filename = self._validate_filename(upload.filename)
        self._validate_content_type(upload.content_type)

        upload_id = str(uuid4())
        target_dir = self.write_root / agent_id / upload_id
        target_dir.mkdir(parents=True, exist_ok=False)
        temporary_path = target_dir / ".source.uploading"
        final_path = target_dir / "source.docx"
        digest = hashlib.sha256()
        size_bytes = 0
        signature = b""

        try:
            with temporary_path.open("xb") as destination:
                while chunk := await upload.read(_CHUNK_SIZE):
                    size_bytes += len(chunk)
                    if size_bytes > self.max_upload_bytes:
                        raise UploadTooLargeError(
                            f"Upload exceeds the {self.max_upload_bytes}-byte limit"
                        )
                    if len(signature) < 4:
                        signature += chunk[: 4 - len(signature)]
                    digest.update(chunk)
                    destination.write(chunk)

            if signature != b"PK\x03\x04":
                raise UploadValidationError("Uploaded file is not a DOCX ZIP container")

            temporary_path.replace(final_path)
        except Exception:
            logger.exception("upload.store_failed agent_id=%s filename=%s", agent_id, filename)
            temporary_path.unlink(missing_ok=True)
            raise

        agent_path = str(self.agent_root / agent_id / upload_id / "source.docx")
        stored = StoredUpload(
            upload_id=upload_id,
            agent_id=agent_id,
            filename=filename,
            size_bytes=size_bytes,
            sha256=digest.hexdigest(),
            agent_path=agent_path,
        )
        logger.info(
            "upload.stored upload_id=%s agent_id=%s filename=%s size_bytes=%s",
            stored.upload_id,
            stored.agent_id,
            stored.filename,
            stored.size_bytes,
        )
        return stored

    @staticmethod
    def _validate_agent_id(agent_id: str) -> None:
        if not _AGENT_ID_PATTERN.fullmatch(agent_id):
            raise UploadValidationError("agent_id must contain only letters, digits, underscores, or hyphens")

    @staticmethod
    def _validate_filename(filename: str | None) -> str:
        if not filename:
            raise UploadValidationError("A filename is required")
        normalized_name = filename.replace("\\", "/")
        safe_name = normalized_name.rsplit("/", maxsplit=1)[-1]
        if safe_name != normalized_name:
            raise UploadValidationError("Filename must not contain a path")
        if safe_name in {"", ".", ".."} or not safe_name.lower().endswith(".docx"):
            raise UploadValidationError("Only .docx files are accepted")
        return safe_name

    @staticmethod
    def _validate_content_type(content_type: str | None) -> None:
        if content_type and content_type not in _DOCX_CONTENT_TYPES:
            raise UploadValidationError("Content-Type must be a DOCX MIME type")
