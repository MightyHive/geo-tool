from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from jobs.ai_impact_baseline_refit import run_job


def test_split_gcs_uri() -> None:
    assert run_job._split_gcs_uri("gs://bucket/path/input.tar.gz") == (
        "bucket",
        "path/input.tar.gz",
    )
    with pytest.raises(ValueError):
        run_job._split_gcs_uri("/tmp/input.tar.gz")


def test_safe_extract_rejects_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        member = tarfile.TarInfo("../escape.txt")
        payload = b"escape"
        member.size = len(payload)
        bundle.addfile(member, io.BytesIO(payload))

    with pytest.raises(ValueError, match="Unsafe archive member"):
        run_job._safe_extract(archive, tmp_path / "out")


def test_update_current_uses_observed_generation() -> None:
    class Blob:
        generation = 17

        def __init__(self) -> None:
            self.upload = None

        def reload(self) -> None:
            return None

        def upload_from_string(self, value, **kwargs) -> None:
            self.upload = (value, kwargs)

    blob = Blob()

    class Bucket:
        def blob(self, _name):
            return blob

    class Client:
        def bucket(self, _name):
            return Bucket()

    run_job._update_current(
        Client(),
        "gs://models/models/dev/CURRENT",
        "hierarchical-ai-v3",
    )

    assert blob.upload == (
        "hierarchical-ai-v3\n",
        {"content_type": "text/plain", "if_generation_match": 17},
    )
