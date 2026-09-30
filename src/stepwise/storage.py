from __future__ import annotations

from pathlib import Path
from uuid import UUID


class ArtifactNotFoundError(FileNotFoundError):
    pass


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe_name(name: str) -> str:
        candidate = Path(name).name
        if candidate != name or candidate in {"", ".", ".."}:
            raise ValueError("Invalid artifact name")
        return candidate

    def put(self, analysis_id: UUID, name: str, content: bytes) -> str:
        safe_name = self._safe_name(name)
        relative = Path(str(analysis_id)) / safe_name
        target = (self.root / relative).resolve()
        if self.root not in target.parents:
            raise ValueError("Artifact path escapes storage root")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return relative.as_posix()

    def resolve(self, storage_key: str) -> Path:
        target = (self.root / storage_key).resolve()
        if self.root != target and self.root not in target.parents:
            raise ValueError("Artifact path escapes storage root")
        return target

    def read(self, storage_key: str) -> bytes:
        target = self.resolve(storage_key)
        if not target.is_file():
            raise ArtifactNotFoundError(storage_key)
        return target.read_bytes()
