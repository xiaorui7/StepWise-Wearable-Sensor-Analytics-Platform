from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Protocol
from uuid import UUID


class ArtifactNotFoundError(FileNotFoundError):
    pass


class StorageBackend(Protocol):
    def put(self, analysis_id: UUID, name: str, content: bytes) -> str: ...

    def publish(
        self, analysis_id: UUID, token: str, artifacts: dict[str, tuple[str, bytes]]
    ) -> dict[str, tuple[str, str]]: ...

    def read(self, storage_key: str) -> bytes: ...

    def check(self) -> None: ...


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe_name(name: str) -> str:
        candidate = Path(name).name
        if candidate != name or candidate in {"", ".", ".."} or any(c in name for c in "\\:/\x00"):
            raise ValueError("Invalid artifact name")
        return candidate

    def put(self, analysis_id: UUID, name: str, content: bytes) -> str:
        safe_name = self._safe_name(name)
        relative = Path(str(analysis_id)) / safe_name
        target = (self.root / relative).resolve()
        if self.root not in target.parents:
            raise ValueError("Artifact path escapes storage root")
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=".upload-")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return relative.as_posix()

    def publish(
        self, analysis_id: UUID, token: str, artifacts: dict[str, tuple[str, bytes]]
    ) -> dict[str, tuple[str, str]]:
        token = str(UUID(token))
        parent = self.root / str(analysis_id)
        parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=parent))
        final = parent / token
        try:
            for name, (_, content) in artifacts.items():
                target = staging / self._safe_name(name)
                with target.open("wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
            # Same-filesystem directory rename publishes the complete attempt together.
            staging.rename(final)
            return {
                name: (media_type, (final / name).relative_to(self.root).as_posix())
                for name, (media_type, _) in artifacts.items()
            }
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    def resolve(self, storage_key: str) -> Path:
        if "://" in storage_key:
            raise ValueError("Storage key belongs to a different backend")
        target = (self.root / storage_key).resolve()
        if self.root != target and self.root not in target.parents:
            raise ValueError("Artifact path escapes storage root")
        return target

    def read(self, storage_key: str) -> bytes:
        target = self.resolve(storage_key)
        if not target.is_file():
            raise ArtifactNotFoundError(storage_key)
        return target.read_bytes()

    def check(self) -> None:
        if not self.root.is_dir():
            raise OSError("Artifact directory is unavailable")


def create_store(settings: object) -> StorageBackend:
    from stepwise.settings import Settings

    assert isinstance(settings, Settings)
    if settings.storage_backend == "s3":
        from stepwise.s3_storage import S3ArtifactStore

        return S3ArtifactStore(settings)
    return ArtifactStore(settings.artifact_root)
