from __future__ import annotations

import hashlib
import stat
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ..exceptions import UnsafeArchiveError, UnsupportedSourceFormatError


@dataclass(frozen=True)
class ArchiveLimits:
    max_archive_bytes: int = 100 * 1024 * 1024
    max_entries: int = 256
    max_entry_bytes: int = 50 * 1024 * 1024
    max_expanded_bytes: int = 200 * 1024 * 1024
    max_compression_ratio: float = 100.0


@dataclass(frozen=True)
class SourceDocument:
    name: str
    content: bytes
    source_fingerprint: str


class SafeArchiveReader:
    """Reads one bounded conversations document without extracting an archive."""

    def __init__(self, limits: ArchiveLimits | None = None) -> None:
        self._limits = limits or ArchiveLimits()

    def read_document(
        self,
        source: Path,
        *,
        document_name: str,
        allow_direct_json: bool = False,
    ) -> SourceDocument:
        if PurePosixPath(document_name).name != document_name or not document_name:
            raise ValueError("document_name must be one safe basename")
        path = source.expanduser().absolute()
        self._validate_source_file(path)
        if path.suffix.lower() == ".json":
            if not allow_direct_json:
                raise UnsupportedSourceFormatError(
                    "This provider requires its source document inside a ZIP archive"
                )
            content = self._read_bounded_file(path, self._limits.max_entry_bytes)
            return SourceDocument(
                name=path.name,
                content=content,
                source_fingerprint=f"sha256:{hashlib.sha256(content).hexdigest()}",
            )
        if path.suffix.lower() != ".zip":
            raise UnsupportedSourceFormatError(
                "The selected provider source must be a ZIP archive or supported JSON document"
            )
        return self._read_zip_document(path, document_name)

    def _validate_source_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            raise UnsafeArchiveError("The selected source is not a readable regular file")
        if path.is_symlink():
            raise UnsafeArchiveError("Symbolic-link source files are not accepted")
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise UnsafeArchiveError("The selected source cannot be inspected") from exc
        if size <= 0:
            raise UnsafeArchiveError("The selected source is empty")
        if size > self._limits.max_archive_bytes:
            raise UnsafeArchiveError("The selected source exceeds the archive size limit")

    def _read_zip_document(self, path: Path, document_name: str) -> SourceDocument:
        fingerprint = self._fingerprint(path)
        try:
            with zipfile.ZipFile(path) as archive:
                files = self._validate_members(archive.infolist())
                candidates = [
                    info
                    for info in files
                    if PurePosixPath(info.filename).name == document_name
                ]
                if len(candidates) != 1:
                    raise UnsupportedSourceFormatError(
                        f"The archive must contain exactly one {document_name} document"
                    )
                content = self._read_member(archive, candidates[0])
        except zipfile.BadZipFile as exc:
            raise UnsafeArchiveError("The selected source is not a valid ZIP archive") from exc
        except OSError as exc:
            raise UnsafeArchiveError("The selected archive cannot be read") from exc
        if self._fingerprint(path) != fingerprint:
            raise UnsafeArchiveError("The selected archive changed while it was being read")

        return SourceDocument(
            name=candidates[0].filename,
            content=content,
            source_fingerprint=fingerprint,
        )

    def _validate_members(self, members: list[zipfile.ZipInfo]) -> list[zipfile.ZipInfo]:
        if len(members) > self._limits.max_entries:
            raise UnsafeArchiveError("The archive contains too many entries")

        normalized_names: set[str] = set()
        files: list[zipfile.ZipInfo] = []
        expanded_bytes = 0
        for info in members:
            normalized = self._validate_member_name(info)
            if normalized in normalized_names:
                raise UnsafeArchiveError("The archive contains colliding entry names")
            normalized_names.add(normalized)

            mode = (info.external_attr >> 16) & 0o170000
            if stat.S_ISLNK(mode):
                raise UnsafeArchiveError("The archive contains a symbolic link")
            if mode and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise UnsafeArchiveError("The archive contains an unsupported member type")
            if info.flag_bits & 0x1:
                raise UnsafeArchiveError("Encrypted archive entries are not supported")
            if info.is_dir():
                continue

            if info.file_size > self._limits.max_entry_bytes:
                raise UnsafeArchiveError("An archive entry exceeds the entry size limit")
            expanded_bytes += info.file_size
            if expanded_bytes > self._limits.max_expanded_bytes:
                raise UnsafeArchiveError("The archive exceeds the expanded size limit")
            if info.file_size:
                ratio = info.file_size / max(info.compress_size, 1)
                if ratio > self._limits.max_compression_ratio:
                    raise UnsafeArchiveError("An archive entry exceeds the compression-ratio limit")
            files.append(info)
        return files

    @staticmethod
    def _validate_member_name(info: zipfile.ZipInfo) -> str:
        raw_name = info.filename
        if not raw_name or "\\" in raw_name or raw_name.startswith("/"):
            raise UnsafeArchiveError("The archive contains an unsafe entry path")
        name = raw_name[:-1] if info.is_dir() and raw_name.endswith("/") else raw_name
        normalized = unicodedata.normalize("NFC", name)
        raw_parts = normalized.split("/")
        parts = PurePosixPath(normalized).parts
        if not parts or any(part in {"", ".", ".."} for part in raw_parts):
            raise UnsafeArchiveError("The archive contains an unsafe entry path")
        if PurePosixPath(normalized).is_absolute() or parts[0].endswith(":"):
            raise UnsafeArchiveError("The archive contains an absolute entry path")
        return normalized

    def _read_member(self, archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
        chunks: list[bytes] = []
        total = 0
        with archive.open(info, "r") as stream:
            while chunk := stream.read(64 * 1024):
                total += len(chunk)
                if total > self._limits.max_entry_bytes:
                    raise UnsafeArchiveError("An archive entry exceeded its actual-byte limit")
                chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _read_bounded_file(path: Path, limit: int) -> bytes:
        try:
            with path.open("rb") as stream:
                content = stream.read(limit + 1)
        except OSError as exc:
            raise UnsafeArchiveError("The selected JSON document cannot be read") from exc
        if len(content) > limit:
            raise UnsafeArchiveError("The selected JSON document exceeds the entry size limit")
        return content

    def _fingerprint(self, path: Path) -> str:
        digest = hashlib.sha256()
        total = 0
        try:
            with path.open("rb") as stream:
                while chunk := stream.read(64 * 1024):
                    total += len(chunk)
                    if total > self._limits.max_archive_bytes:
                        raise UnsafeArchiveError(
                            "The source exceeded the archive size limit while reading"
                        )
                    digest.update(chunk)
        except OSError as exc:
            raise UnsafeArchiveError("The selected source cannot be fingerprinted") from exc
        return f"sha256:{digest.hexdigest()}"
