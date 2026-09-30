from pathlib import Path
from uuid import uuid4

import pytest

from stepwise.storage import ArtifactStore


def test_artifact_store_persists_content(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    key = store.put(uuid4(), "report.html", b"report")
    assert store.read(key) == b"report"


@pytest.mark.parametrize("name", ["../secret.txt", "folder/report.html", "..", ""])
def test_artifact_store_rejects_path_traversal(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError):
        ArtifactStore(tmp_path).put(uuid4(), name, b"unsafe")
