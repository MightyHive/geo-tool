"""Versioned, NumPy-only hierarchical model artifact access."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Literal

import numpy as np
import pandas as pd


ARTIFACT_SCHEMA_VERSION = "ai-impact-hierarchical-v1"
DEFAULT_CURRENT_REFRESH_SECONDS = 300.0
DEFAULT_GCS_CACHE_ROOT = Path("/tmp/ai-impact-model-artifacts")
MODEL_CATEGORIES = (
    "advertiser-retail",
    "advertiser-services",
    "publisher",
)
Outcome = Literal["seo", "direct"]
Specification = Literal["uncapped", "capped"]


class ArtifactCompatibilityError(ValueError):
    """Raised when an artifact is missing or incompatible with the scorer."""


_CACHE_LOCK = threading.Lock()


def artifact_root() -> Path | str:
    configured = (os.environ.get("AI_IMPACT_MODEL_ARTIFACT_ROOT") or "").strip()
    if configured:
        return configured if configured.startswith("gs://") else Path(configured)
    return Path(__file__).resolve().parent / "model_artifacts"


def signal_artifact_root() -> Path | None:
    configured = (os.environ.get("AI_IMPACT_SIGNAL_ARTIFACT_ROOT") or "").strip()
    return Path(configured) if configured else None


def _current_version_dir(root: Path) -> Path:
    pointer = root / "CURRENT"
    if not pointer.is_file():
        raise ArtifactCompatibilityError(f"Missing artifact pointer {pointer}")
    version = pointer.read_text(encoding="utf-8").strip()
    if not version or Path(version).name != version:
        raise ArtifactCompatibilityError(f"Invalid artifact pointer {pointer}")
    return root / version


def _valid_version(value: str, *, source: str) -> str:
    version = value.strip()
    if not version or Path(version).name != version or version in {".", ".."}:
        raise ArtifactCompatibilityError(f"Invalid artifact version in {source}")
    return version


def _parse_gcs_root(uri: str) -> tuple[str, str]:
    rest = uri.removeprefix("gs://")
    bucket, _separator, prefix = rest.partition("/")
    if not bucket:
        raise ArtifactCompatibilityError(f"Invalid GCS artifact root {uri!r}")
    return bucket, prefix.strip("/")


def _cache_root(uri: str) -> Path:
    configured = (os.environ.get("AI_IMPACT_MODEL_CACHE_ROOT") or "").strip()
    base = Path(configured) if configured else DEFAULT_GCS_CACHE_ROOT
    root_id = hashlib.sha256(uri.rstrip("/").encode("utf-8")).hexdigest()[:20]
    return base / root_id


def _refresh_seconds() -> float:
    raw = (
        os.environ.get("AI_IMPACT_MODEL_CURRENT_REFRESH_SECONDS")
        or str(DEFAULT_CURRENT_REFRESH_SECONDS)
    )
    try:
        value = float(raw)
    except ValueError as exc:
        raise ArtifactCompatibilityError(
            "AI_IMPACT_MODEL_CURRENT_REFRESH_SECONDS must be numeric"
        ) from exc
    if value < 0:
        raise ArtifactCompatibilityError(
            "AI_IMPACT_MODEL_CURRENT_REFRESH_SECONDS must be non-negative"
        )
    return value


def _storage_client() -> Any:
    from google.cloud import storage  # type: ignore

    return storage.Client()


@contextmanager
def _cache_lock(cache_root: Path) -> Iterator[None]:
    cache_root.mkdir(parents=True, exist_ok=True)
    lock_path = cache_root / ".lock"
    with _CACHE_LOCK:
        with lock_path.open("a+b") as lock_file:
            try:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                yield
            finally:
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                except (NameError, OSError):
                    pass


def _blob_name(prefix: str, *parts: str) -> str:
    return "/".join(part.strip("/") for part in (prefix, *parts) if part)


def _read_cached_state(cache_root: Path) -> dict[str, Any] | None:
    path = cache_root / ".current.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return value if isinstance(value, dict) else None


def _write_cached_state(cache_root: Path, version: str) -> None:
    state = {"version": version, "checked_at": time.time()}
    temporary = cache_root / f".current.{os.getpid()}.{threading.get_ident()}.tmp"
    temporary.write_text(json.dumps(state), encoding="utf-8")
    os.replace(temporary, cache_root / ".current.json")


def _cached_version(state: dict[str, Any] | None, cache_root: Path) -> Path | None:
    if state is None:
        return None
    try:
        version = _valid_version(str(state["version"]), source="cache state")
    except (KeyError, ArtifactCompatibilityError):
        return None
    path = cache_root / version
    return path if path.is_dir() else None


def _safe_artifact_file(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ArtifactCompatibilityError(f"Invalid artifact filename in {field}")
    name = value.strip()
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts or "." in relative.parts:
        raise ArtifactCompatibilityError(f"Unsafe artifact filename {name!r}")
    return relative.as_posix()


def _manifest_hashes(manifest: dict[str, Any]) -> dict[str, str]:
    raw: Any = None
    for field in ("sha256", "sha256s", "file_sha256", "checksums"):
        if field in manifest:
            raw = manifest[field]
            break
    if raw is None and isinstance(manifest.get("files"), dict):
        raw = {
            (value.get("path") or value.get("filename") or filename): value
            for filename, value in manifest["files"].items()
            if isinstance(value, dict) and value.get("sha256")
        }
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ArtifactCompatibilityError("Manifest SHA-256 hashes must be an object")
    hashes: dict[str, str] = {}
    for filename, value in raw.items():
        name = _safe_artifact_file(filename, field="SHA-256 hashes")
        if isinstance(value, dict):
            value = value.get("sha256")
        digest = str(value or "").lower()
        if digest.startswith("sha256:"):
            digest = digest[7:]
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ArtifactCompatibilityError(f"Invalid SHA-256 for {name}")
        hashes[name] = digest
    return hashes


def _required_files(manifest: dict[str, Any], *, require_refit: bool) -> set[str]:
    posterior_files = manifest.get("posterior_files")
    if not isinstance(posterior_files, dict):
        raise ArtifactCompatibilityError("Artifact posterior_files must be an object")
    required = {"portfolio_signal.csv.gz"}
    for outcome in ("seo", "direct"):
        for specification in ("uncapped", "capped"):
            key = f"{outcome}_{specification}"
            required.add(
                _safe_artifact_file(
                    posterior_files.get(key), field=f"posterior_files.{key}"
                )
            )
    if require_refit:
        required.update({"training_panel.csv.gz", "training_sites.csv"})
    return required


def _read_manifest(path: Path) -> dict[str, Any]:
    manifest_path = path / "manifest.json"
    if not manifest_path.is_file():
        raise ArtifactCompatibilityError(f"Missing artifact manifest {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ArtifactCompatibilityError(
            f"Invalid artifact manifest {manifest_path}"
        ) from exc
    if not isinstance(manifest, dict):
        raise ArtifactCompatibilityError("Artifact manifest must be an object")
    return manifest


def _validate_manifest(
    manifest: dict[str, Any], path: Path, *, expected_version: str | None
) -> None:
    if manifest.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
        raise ArtifactCompatibilityError(
            f"Unsupported artifact schema {manifest.get('schema_version')!r}"
        )
    if manifest.get("promoted") is not True:
        raise ArtifactCompatibilityError(
            f"Artifact {manifest.get('model_version')} was not promoted"
        )
    model_version = manifest.get("model_version")
    if not isinstance(model_version, str) or not model_version:
        raise ArtifactCompatibilityError("Artifact model_version must be a string")
    if expected_version is not None and model_version != expected_version:
        raise ArtifactCompatibilityError(
            f"Artifact model_version {model_version!r} does not match "
            f"version directory {expected_version!r}"
        )
    categories = tuple(manifest.get("categories") or ())
    if set(categories) != set(MODEL_CATEGORIES) or len(categories) != len(
        MODEL_CATEGORIES
    ):
        raise ArtifactCompatibilityError(
            f"Artifact taxonomy must be exactly {MODEL_CATEGORIES}; got {categories}"
        )


def _verify_hashes(path: Path, hashes: dict[str, str]) -> None:
    for filename, expected in hashes.items():
        file_path = path / filename
        if not file_path.is_file():
            raise ArtifactCompatibilityError(f"Missing hashed artifact file {file_path}")
        digest = hashlib.sha256()
        with file_path.open("rb") as artifact_file:
            for chunk in iter(lambda: artifact_file.read(1024 * 1024), b""):
                digest.update(chunk)
        actual = digest.hexdigest()
        if actual != expected:
            raise ArtifactCompatibilityError(
                f"SHA-256 mismatch for artifact file {filename}"
            )


def _load_and_validate(
    path: Path, *, expected_version: str | None = None, require_refit: bool = False
) -> HierarchicalArtifact:
    manifest = _read_manifest(path)
    _validate_manifest(manifest, path, expected_version=expected_version)
    for filename in _required_files(manifest, require_refit=require_refit):
        file_path = path / filename
        if not file_path.is_file():
            raise ArtifactCompatibilityError(f"Missing artifact file {file_path}")
    _verify_hashes(path, _manifest_hashes(manifest))
    artifact = HierarchicalArtifact(path=path, manifest=manifest)
    for outcome in ("seo", "direct"):
        for specification in ("uncapped", "capped"):
            artifact.posterior(outcome, specification)
    artifact.portfolio_signal()
    return artifact


def _download_version(
    uri: str, version: str, cache_root: Path, *, bucket: Any | None = None
) -> Path:
    destination = cache_root / version
    if destination.is_dir():
        _load_and_validate(
            destination, expected_version=version, require_refit=True
        )
        return destination

    bucket_name, prefix = _parse_gcs_root(uri)
    if bucket is None:
        bucket = _storage_client().bucket(bucket_name)
    staging = Path(tempfile.mkdtemp(prefix=f".{version}.", dir=cache_root))
    try:
        manifest_blob = bucket.blob(_blob_name(prefix, version, "manifest.json"))
        manifest_blob.download_to_filename(str(staging / "manifest.json"))
        manifest = _read_manifest(staging)
        _validate_manifest(manifest, staging, expected_version=version)
        files = _required_files(manifest, require_refit=True)
        files.update(_manifest_hashes(manifest))
        files.discard("manifest.json")
        for filename in sorted(files):
            target = staging / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            bucket.blob(_blob_name(prefix, version, filename)).download_to_filename(
                str(target)
            )
        _load_and_validate(staging, expected_version=version, require_refit=True)
        try:
            os.replace(staging, destination)
        except OSError:
            if not destination.is_dir():
                raise
        return destination
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _gcs_current_artifact_dir(uri: str, configured_version: str) -> Path:
    bucket_name, prefix = _parse_gcs_root(uri)
    cache_root = _cache_root(uri)
    with _cache_lock(cache_root):
        state = _read_cached_state(cache_root)
        previous = _cached_version(state, cache_root)
        if configured_version:
            version = _valid_version(
                configured_version, source="AI_IMPACT_MODEL_VERSION"
            )
            try:
                return _download_version(uri, version, cache_root)
            except Exception as exc:
                if isinstance(exc, ArtifactCompatibilityError):
                    raise
                raise ArtifactCompatibilityError(
                    f"Unable to load GCS model artifact version {version!r} from {uri}"
                ) from exc

        if previous is not None:
            try:
                checked_at = float((state or {}).get("checked_at", 0))
            except (TypeError, ValueError):
                checked_at = 0
            if time.time() - checked_at < _refresh_seconds():
                return previous

        try:
            bucket = _storage_client().bucket(bucket_name)
            pointer_blob = bucket.blob(_blob_name(prefix, "CURRENT"))
            version = _valid_version(
                pointer_blob.download_as_text(), source=f"{uri.rstrip('/')}/CURRENT"
            )
            destination = _download_version(
                uri, version, cache_root, bucket=bucket
            )
            _write_cached_state(cache_root, version)
            return destination
        except Exception as exc:
            if previous is not None:
                _write_cached_state(cache_root, previous.name)
                return previous
            if isinstance(exc, ArtifactCompatibilityError):
                raise
            raise ArtifactCompatibilityError(
                f"Unable to load GCS model artifact from {uri}"
            ) from exc


def current_artifact_dir(root: Path | str | None = None) -> Path:
    base = root or artifact_root()
    configured_version = (os.environ.get("AI_IMPACT_MODEL_VERSION") or "").strip()
    if isinstance(base, str) and base.startswith("gs://"):
        return _gcs_current_artifact_dir(base.rstrip("/"), configured_version)
    base = Path(base)
    if configured_version:
        return base / _valid_version(
            configured_version, source="AI_IMPACT_MODEL_VERSION"
        )
    return _current_version_dir(base)


@dataclass(frozen=True)
class HierarchicalArtifact:
    path: Path
    manifest: dict[str, Any]

    @property
    def model_version(self) -> str:
        return str(self.manifest["model_version"])

    @property
    def categories(self) -> tuple[str, ...]:
        return tuple(self.manifest["categories"])

    @property
    def signal_version(self) -> str:
        external = self._external_signal_bundle()
        if external is not None:
            _path, manifest = external
            return str(manifest["signal_version"])
        return str(
            self.manifest.get("signal_version")
            or self.manifest.get("latest_completed_portfolio_week")
        )

    def _external_signal_bundle(self) -> tuple[Path, dict[str, Any]] | None:
        root = signal_artifact_root()
        if root is None:
            return None
        path = _current_version_dir(root)
        manifest_path = path / "manifest.json"
        if not manifest_path.is_file():
            raise ArtifactCompatibilityError(
                f"Missing portfolio signal manifest {manifest_path}"
            )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != "ai-impact-portfolio-signal-v1":
            raise ArtifactCompatibilityError(
                f"Unsupported portfolio signal schema {manifest.get('schema_version')!r}"
            )
        if manifest.get("model_version") != self.model_version:
            raise ArtifactCompatibilityError(
                "Portfolio signal was produced for a different model version"
            )
        return path, manifest

    def category_index(self, category: str) -> int:
        validate_category(category)
        try:
            return self.categories.index(category)
        except ValueError as exc:
            raise ArtifactCompatibilityError(
                f"Category {category!r} is absent from model {self.model_version}"
            ) from exc

    def posterior(
        self, outcome: Outcome, specification: Specification
    ) -> dict[str, np.ndarray]:
        key = f"{outcome}_{specification}"
        filename = (self.manifest.get("posterior_files") or {}).get(key)
        if not filename:
            raise ArtifactCompatibilityError(f"Artifact has no posterior for {key}")
        path = self.path / str(filename)
        if not path.is_file():
            raise ArtifactCompatibilityError(f"Missing posterior file {path}")
        with np.load(path, allow_pickle=False) as stored:
            arrays = {name: stored[name] for name in stored.files}
        beta = arrays.get("beta_ai_cat")
        if beta is None or beta.ndim != 2 or beta.shape[1] != len(self.categories):
            raise ArtifactCompatibilityError(
                f"{key} beta_ai_cat shape is incompatible with category order"
            )
        return arrays

    def portfolio_signal(self) -> pd.DataFrame:
        external = self._external_signal_bundle()
        path = (
            external[0] / "portfolio_signal.csv.gz"
            if external is not None
            else self.path / "portfolio_signal.csv.gz"
        )
        if not path.is_file():
            raise ArtifactCompatibilityError(f"Missing portfolio signal {path}")
        frame = pd.read_csv(path, parse_dates=["week_start"])
        required = {
            "week_start",
            "ai_sessions_total",
            "all_sessions_total",
            "ai_share",
            "ai_signal",
            "ai_signal_capped",
        }
        missing = required - set(frame.columns)
        if missing:
            raise ArtifactCompatibilityError(
                f"Portfolio signal is missing columns: {sorted(missing)}"
            )
        return frame.sort_values("week_start").reset_index(drop=True)

    def baseline(self, specification: Specification) -> float:
        try:
            return float(self.manifest["baseline_signal"][specification])
        except (KeyError, TypeError, ValueError) as exc:
            raise ArtifactCompatibilityError(
                f"Artifact has no {specification} baseline signal"
            ) from exc


def validate_category(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in MODEL_CATEGORIES:
        raise ValueError(
            f"Invalid model category {value!r}; expected one of {MODEL_CATEGORIES}"
        )
    return normalized


def load_artifact(path: Path | str | None = None) -> HierarchicalArtifact:
    if path is None:
        artifact_path = current_artifact_dir()
    elif isinstance(path, str) and path.startswith("gs://"):
        artifact_path = current_artifact_dir(path)
    else:
        artifact_path = Path(path)
    return _load_and_validate(artifact_path)


def transform_ai_share(
    ai_share: np.ndarray | pd.Series,
    transform: dict[str, Any],
    *,
    capped: bool,
) -> np.ndarray:
    """Apply the frozen training transform without importing research code."""
    share = np.asarray(ai_share, dtype=float)
    if np.any(~np.isfinite(share)) or np.any(share < 0):
        raise ValueError("AI share must contain finite non-negative values")
    scaled = np.log1p(share / float(transform["share_scale"]))
    if capped:
        positive = share > 0
        scaled = scaled.copy()
        scaled[positive] = np.clip(
            scaled[positive],
            float(transform["cap_low"]),
            float(transform["cap_high"]),
        )
        scaled[~positive] = 0.0
    return (scaled - float(transform["transform_mean"])) / float(
        transform["transform_std"]
    )
