import gzip
import hashlib
import io
import json
import threading
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backend.ai_impact import model_artifact


CATEGORIES = [
    "advertiser-retail",
    "advertiser-services",
    "publisher",
]


def _npz_bytes() -> bytes:
    output = io.BytesIO()
    np.savez_compressed(
        output, beta_ai_cat=np.tile([[0.03, -0.01, 0.02]], (10, 1))
    )
    return output.getvalue()


def _bundle(version: str, *, bad_hash: bool = False) -> dict[str, bytes]:
    posterior_files = {}
    files: dict[str, bytes] = {}
    posterior = _npz_bytes()
    for outcome in ("seo", "direct"):
        for specification in ("uncapped", "capped"):
            filename = f"posterior_{outcome}_{specification}.npz"
            posterior_files[f"{outcome}_{specification}"] = filename
            files[filename] = posterior

    signal = pd.DataFrame(
        {
            "week_start": ["2026-01-04"],
            "ai_sessions_total": [10],
            "all_sessions_total": [1000],
            "ai_share": [0.01],
            "ai_signal": [0.5],
            "ai_signal_capped": [0.5],
        }
    ).to_csv(index=False)
    files["portfolio_signal.csv.gz"] = gzip.compress(signal.encode())
    files["training_panel.csv.gz"] = gzip.compress(b"date,site_id\n")
    files["training_sites.csv"] = b"site_id,category\n"
    hashes = {
        name: hashlib.sha256(content).hexdigest() for name, content in files.items()
    }
    if bad_hash:
        hashes["training_sites.csv"] = "0" * 64
    manifest = {
        "schema_version": model_artifact.ARTIFACT_SCHEMA_VERSION,
        "model_version": version,
        "promoted": True,
        "categories": CATEGORIES,
        "posterior_files": posterior_files,
        "baseline_signal": {"uncapped": 0.0, "capped": 0.0},
        "latest_completed_portfolio_week": "2026-01-04",
        "sha256": hashes,
    }
    files["manifest.json"] = json.dumps(manifest).encode()
    return files


class _FakeBlob:
    def __init__(self, store: dict[str, bytes], name: str, downloads: list[str]):
        self.store = store
        self.name = name
        self.downloads = downloads

    def download_as_text(self) -> str:
        self.downloads.append(self.name)
        return self.store[self.name].decode()

    def download_to_filename(self, filename: str) -> None:
        self.downloads.append(self.name)
        Path(filename).write_bytes(self.store[self.name])


class _FakeBucket:
    def __init__(self, store: dict[str, bytes], downloads: list[str]):
        self.store = store
        self.downloads = downloads

    def blob(self, name: str) -> _FakeBlob:
        return _FakeBlob(self.store, name, self.downloads)


class _FakeClient:
    def __init__(self, store: dict[str, bytes], downloads: list[str]):
        self.store = store
        self.downloads = downloads

    def bucket(self, _name: str) -> _FakeBucket:
        return _FakeBucket(self.store, self.downloads)


def _publish(store: dict[str, bytes], version: str, **kwargs) -> None:
    store.update(
        {f"models/{version}/{name}": value for name, value in _bundle(version, **kwargs).items()}
    )
    store["models/CURRENT"] = f"{version}\n".encode()


@pytest.fixture
def gcs_loader(tmp_path: Path, monkeypatch):
    store: dict[str, bytes] = {}
    downloads: list[str] = []
    client = _FakeClient(store, downloads)
    monkeypatch.setenv("AI_IMPACT_MODEL_ARTIFACT_ROOT", "gs://test-bucket/models")
    monkeypatch.setenv("AI_IMPACT_MODEL_CACHE_ROOT", str(tmp_path / "cache"))
    monkeypatch.setattr(model_artifact, "_storage_client", lambda: client)
    return store, downloads


def test_gcs_bundle_downloads_atomically_and_reuses_cache(
    gcs_loader, monkeypatch
) -> None:
    store, downloads = gcs_loader
    _publish(store, "model-v1")
    monkeypatch.setenv("AI_IMPACT_MODEL_CURRENT_REFRESH_SECONDS", "3600")

    first = model_artifact.load_artifact()
    second = model_artifact.load_artifact()

    assert first.model_version == second.model_version == "model-v1"
    assert first.path == second.path
    assert first.path.parent.name == hashlib.sha256(
        b"gs://test-bucket/models"
    ).hexdigest()[:20]
    assert downloads.count("models/CURRENT") == 1
    assert downloads.count("models/model-v1/manifest.json") == 1
    assert not list(first.path.parent.glob(".model-v1.*"))


def test_gcs_current_refresh_promotes_new_version_and_retains_old(
    gcs_loader, monkeypatch
) -> None:
    store, _downloads = gcs_loader
    monkeypatch.setenv("AI_IMPACT_MODEL_CURRENT_REFRESH_SECONDS", "0")
    _publish(store, "model-v1")
    old = model_artifact.load_artifact()
    _publish(store, "model-v2")

    current = model_artifact.load_artifact()

    assert current.model_version == "model-v2"
    assert old.path.is_dir()
    assert current.path.is_dir()


def test_invalid_new_promotion_rolls_back_to_cached_version(
    gcs_loader, monkeypatch
) -> None:
    store, _downloads = gcs_loader
    monkeypatch.setenv("AI_IMPACT_MODEL_CURRENT_REFRESH_SECONDS", "0")
    _publish(store, "model-v1")
    old = model_artifact.load_artifact()
    _publish(store, "model-v2", bad_hash=True)

    loaded = model_artifact.load_artifact()

    assert loaded.model_version == "model-v1"
    assert loaded.path == old.path
    assert not (old.path.parent / "model-v2").exists()


def test_invalid_first_gcs_bundle_is_rejected(gcs_loader) -> None:
    store, _downloads = gcs_loader
    _publish(store, "model-v1", bad_hash=True)

    with pytest.raises(
        model_artifact.ArtifactCompatibilityError, match="SHA-256 mismatch"
    ):
        model_artifact.load_artifact()


def test_gcs_rejects_model_version_directory_mismatch(gcs_loader) -> None:
    store, _downloads = gcs_loader
    _publish(store, "model-v1")
    manifest_name = "models/model-v1/manifest.json"
    manifest = json.loads(store[manifest_name])
    manifest["model_version"] = "different-version"
    store[manifest_name] = json.dumps(manifest).encode()

    with pytest.raises(
        model_artifact.ArtifactCompatibilityError, match="does not match"
    ):
        model_artifact.load_artifact()


def test_concurrent_loaders_never_redownload_partial_version(
    gcs_loader, monkeypatch
) -> None:
    store, downloads = gcs_loader
    _publish(store, "model-v1")
    monkeypatch.setenv("AI_IMPACT_MODEL_CURRENT_REFRESH_SECONDS", "3600")
    loaded = []

    threads = [
        threading.Thread(target=lambda: loaded.append(model_artifact.load_artifact()))
        for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert [artifact.model_version for artifact in loaded] == ["model-v1"] * 4
    assert downloads.count("models/model-v1/manifest.json") == 1
