"""Validate an installed pylopdf OCR model distribution."""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from importlib import metadata
from pathlib import Path

import pylopdf_ocr_models

_DISTRIBUTION = "pylopdf-ocr-models"
_EXPECTED_ARTIFACTS = {
    "PP-OCRv6_det_small.rten",
    "PP-OCRv6_rec_small.rten",
    "ppocrv6_dict.txt",
}
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _require(*, condition: bool, message: str) -> None:
    """Raise a useful smoke-test failure instead of relying on optimized asserts."""
    if not condition:
        raise RuntimeError(message)


def _sha256(path: Path) -> str:
    """Hash one potentially large artifact without reading it all at once."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest(root: Path) -> dict[str, str]:
    """Parse and validate the installed artifact manifest."""
    entries: dict[str, str] = {}
    for line in (root / "SHA256SUMS").read_text(encoding="ascii").splitlines():
        try:
            expected, filename = line.split("  ", maxsplit=1)
        except ValueError as error:
            message = f"invalid SHA256SUMS entry: {line!r}"
            raise RuntimeError(message) from error
        if _SHA256.fullmatch(expected) is None:
            message = f"invalid SHA-256 value for {filename!r}: {expected!r}"
            raise RuntimeError(message)
        if filename in entries or Path(filename).name != filename:
            message = f"unsafe or duplicate SHA256SUMS filename: {filename!r}"
            raise RuntimeError(message)
        entries[filename] = expected
    if entries.keys() != _EXPECTED_ARTIFACTS:
        message = f"unexpected artifact manifest: {sorted(entries)}"
        raise RuntimeError(message)
    return entries


def _check_metadata(version: str) -> metadata.Distribution:
    """Validate installed core metadata and return the distribution."""
    distribution = metadata.distribution(_DISTRIBUTION)
    package_metadata = distribution.metadata
    _require(
        condition=distribution.version == version and pylopdf_ocr_models.__version__ == version,
        message=(
            f"version mismatch: metadata={distribution.version!r}, "
            f"module={pylopdf_ocr_models.__version__!r}, expected={version!r}"
        ),
    )
    _require(
        condition=not distribution.requires,
        message=f"the data-only model package must have no dependencies: {distribution.requires!r}",
    )
    _require(
        condition=package_metadata.get("Requires-Python") == ">=3.10",
        message=f"unexpected Requires-Python: {package_metadata.get('Requires-Python')!r}",
    )
    _require(
        condition=package_metadata.get("License-Expression") == "Apache-2.0",
        message=f"unexpected license expression: {package_metadata.get('License-Expression')!r}",
    )
    license_files = package_metadata.get_all("License-File", [])
    _require(
        condition=set(license_files) == {"LICENSE", "NOTICE"},
        message=f"unexpected license files: {license_files!r}",
    )
    return distribution


def _check_resources() -> tuple[Path, dict[str, Path]]:
    """Validate installed resource names and return their common root."""
    paths = pylopdf_ocr_models.model_paths()
    artifacts = {
        paths.detector.name: paths.detector,
        paths.recognizer.name: paths.recognizer,
        paths.dictionary.name: paths.dictionary,
    }
    _require(
        condition=artifacts.keys() == _EXPECTED_ARTIFACTS,
        message=f"unexpected model paths: {sorted(artifacts)}",
    )
    roots = {path.resolve().parent for path in artifacts.values()}
    _require(
        condition=len(roots) == 1,
        message=f"model artifacts do not share one package directory: {sorted(map(str, roots))}",
    )
    root = roots.pop()
    _require(
        condition=(root / "py.typed").is_file(),
        message="installed model package is missing py.typed",
    )
    return root, artifacts


def _check_artifact_hashes(root: Path, artifacts: dict[str, Path]) -> None:
    """Verify every installed data artifact against the packaged manifest."""
    entries = _manifest(root)
    for filename, path in artifacts.items():
        _require(
            condition=path.is_file(),
            message=f"installed model artifact is missing: {path}",
        )
        actual = _sha256(path)
        _require(
            condition=actual == entries[filename],
            message=f"SHA-256 mismatch for {filename}: {actual}",
        )


def _check_license_payload(distribution: metadata.Distribution) -> None:
    """Verify both declared license files are physically installed."""
    distribution_files = {str(path).replace("\\", "/") for path in distribution.files or ()}
    for license_name in ("LICENSE", "NOTICE"):
        _require(
            condition=any(path.endswith(f".dist-info/licenses/{license_name}") for path in distribution_files),
            message=f"installed distribution is missing {license_name}",
        )


def main() -> None:
    """Check package metadata, resources, and hashes from an installed artifact."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="expected distribution version")
    args = parser.parse_args()

    distribution = _check_metadata(args.version)
    root, artifacts = _check_resources()
    _check_artifact_hashes(root, artifacts)
    _check_license_payload(distribution)
    sys.stdout.write(f"{_DISTRIBUTION} {args.version} artifact smoke test passed\n")


if __name__ == "__main__":
    main()
