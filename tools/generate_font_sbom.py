"""Generate a content-complete SPDX 2.3 SBOM for font distributions."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tarfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator
    from email.message import Message
    from typing import IO

_CHUNK_SIZE = 1024 * 1024
_SOURCE_COMMIT = re.compile(r"[0-9a-f]{40}")
_LICENSE_EXPRESSION = "OFL-1.1"
_REPOSITORY = "https://github.com/yhay81/pylopdf"


@dataclass(frozen=True, slots=True)
class _PackageMetadata:
    name: str
    version: str
    summary: str


def _require(*, condition: bool, message: str) -> None:
    """Raise a useful generation error instead of relying on optimized asserts."""
    if not condition:
        raise RuntimeError(message)


def _hash_stream(source: IO[bytes]) -> tuple[str, str]:
    """Return the SPDX-required SHA-1 and a SHA-256 for one stream."""
    sha1 = hashlib.sha1(usedforsecurity=False)  # SPDX 2.3 package verification requires SHA-1.
    sha256 = hashlib.sha256()
    for chunk in iter(lambda: source.read(_CHUNK_SIZE), b""):
        sha1.update(chunk)
        sha256.update(chunk)
    return sha1.hexdigest(), sha256.hexdigest()


def _sha256_path(path: Path) -> str:
    """Hash one distribution archive without retaining it in memory."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_member_name(raw_name: str, *, archive: Path) -> str:
    """Validate and normalize a distribution member name without extracting it."""
    path = PurePosixPath(raw_name)
    _require(
        condition=bool(raw_name) and "\\" not in raw_name and not path.is_absolute() and ".." not in path.parts,
        message=f"unsafe member path in {archive.name}: {raw_name!r}",
    )
    return path.as_posix()


def _wheel_members(archive_path: Path) -> Iterator[tuple[str, str, str]]:
    """Yield normalized name, SHA-1, and SHA-256 for every wheel file."""
    seen: set[str] = set()
    with zipfile.ZipFile(archive_path) as archive:
        for member in sorted(archive.infolist(), key=lambda item: item.filename):
            if member.is_dir():
                continue
            name = _safe_member_name(member.filename, archive=archive_path)
            _require(condition=name not in seen, message=f"duplicate member in {archive_path.name}: {name!r}")
            seen.add(name)
            with archive.open(member) as source:
                sha1, sha256 = _hash_stream(source)
            yield name, sha1, sha256


def _sdist_members(archive_path: Path) -> Iterator[tuple[str, str, str]]:
    """Yield normalized name, SHA-1, and SHA-256 for every regular sdist file."""
    seen: set[str] = set()
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in sorted(archive.getmembers(), key=lambda item: item.name):
            if member.isdir():
                continue
            _require(condition=member.isfile(), message=f"non-regular member in {archive_path.name}: {member.name!r}")
            name = _safe_member_name(member.name, archive=archive_path)
            _require(condition=name not in seen, message=f"duplicate member in {archive_path.name}: {name!r}")
            seen.add(name)
            source = archive.extractfile(member)
            if source is None:
                message = f"cannot read member in {archive_path.name}: {name!r}"
                raise RuntimeError(message)
            with source:
                sha1, sha256 = _hash_stream(source)
            yield name, sha1, sha256


def _wheel_metadata(wheel_path: Path) -> _PackageMetadata:
    """Read the single Core Metadata payload from a built wheel."""
    with zipfile.ZipFile(wheel_path) as archive:
        members = sorted(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
        _require(
            condition=len(members) == 1,
            message=f"expected exactly one METADATA file in {wheel_path.name}, found {members!r}",
        )
        with archive.open(members[0]) as source:
            metadata = BytesParser(policy=policy.default).parse(source)
    return _PackageMetadata(
        name=_metadata_value(metadata, "Name"),
        version=_metadata_value(metadata, "Version"),
        summary=_metadata_value(metadata, "Summary"),
    )


def _metadata_value(metadata: Message, key: str) -> str:
    """Return one required single-line Core Metadata field."""
    value = metadata.get(key)
    if not isinstance(value, str) or not value:
        message = f"wheel metadata is missing {key}"
        raise RuntimeError(message)
    return value


def _verification_code(sha1_values: list[str]) -> str:
    """Calculate the SPDX 2.3 package verification code."""
    payload = "".join(sorted(sha1_values)).encode("ascii")
    return hashlib.sha1(payload, usedforsecurity=False).hexdigest()


def _spdx_file(
    *,
    artifact_name: str,
    archive_kind: str,
    index: int,
    member: tuple[str, str, str],
) -> dict[str, object]:
    """Build one SPDX file entry for an archive member."""
    member_name, sha1, sha256 = member
    return {
        "fileName": f"./{artifact_name}/{member_name}",
        "SPDXID": f"SPDXRef-File-{archive_kind}-{index:04d}",
        "checksums": [
            {"algorithm": "SHA1", "checksumValue": sha1},
            {"algorithm": "SHA256", "checksumValue": sha256},
        ],
        "licenseConcluded": "NOASSERTION",
        "licenseInfoInFiles": ["NOASSERTION"],
        "copyrightText": "NOASSERTION",
    }


def _spdx_package(
    *,
    artifact_path: Path,
    archive_kind: str,
    metadata: _PackageMetadata,
    files: list[dict[str, object]],
    sha1_values: list[str],
) -> dict[str, object]:
    """Build one SPDX package entry for a wheel or source distribution."""
    return {
        "name": metadata.name,
        "SPDXID": f"SPDXRef-Package-{archive_kind}",
        "versionInfo": metadata.version,
        "packageFileName": artifact_path.name,
        "supplier": "Organization: pylopdf contributors",
        "downloadLocation": "NOASSERTION",
        "filesAnalyzed": True,
        "packageVerificationCode": {"packageVerificationCodeValue": _verification_code(sha1_values)},
        "checksums": [{"algorithm": "SHA256", "checksumValue": _sha256_path(artifact_path)}],
        "homepage": _REPOSITORY,
        "licenseConcluded": _LICENSE_EXPRESSION,
        "licenseDeclared": _LICENSE_EXPRESSION,
        "licenseInfoFromFiles": [_LICENSE_EXPRESSION],
        "copyrightText": "NOASSERTION",
        "summary": metadata.summary,
        "externalRefs": [
            {
                "referenceCategory": "PACKAGE-MANAGER",
                "referenceType": "purl",
                "referenceLocator": f"pkg:pypi/{metadata.name}@{metadata.version}",
            }
        ],
        "primaryPackagePurpose": "ARCHIVE",
        "hasFiles": [file_entry["SPDXID"] for file_entry in files],
    }


def generate_font_sbom(
    dist_dir: Path,
    *,
    source_commit: str,
    source_date_epoch: int,
) -> dict[str, object]:
    """Generate an SPDX document describing both built font distributions."""
    _require(
        condition=_SOURCE_COMMIT.fullmatch(source_commit) is not None,
        message=f"source commit must be a lowercase 40-character SHA-1: {source_commit!r}",
    )
    wheels = sorted(dist_dir.glob("*.whl"))
    sdists = sorted(dist_dir.glob("*.tar.gz"))
    _require(condition=len(wheels) == 1, message=f"expected exactly one wheel in {dist_dir}, found {wheels!r}")
    _require(condition=len(sdists) == 1, message=f"expected exactly one sdist in {dist_dir}, found {sdists!r}")

    metadata = _wheel_metadata(wheels[0])
    created = datetime.fromtimestamp(source_date_epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    packages: list[dict[str, object]] = []
    files: list[dict[str, object]] = []
    relationships: list[dict[str, str]] = []
    for artifact_path, archive_kind, members in (
        (wheels[0], "Wheel", _wheel_members(wheels[0])),
        (sdists[0], "Sdist", _sdist_members(sdists[0])),
    ):
        package_id = f"SPDXRef-Package-{archive_kind}"
        artifact_files: list[dict[str, object]] = []
        sha1_values: list[str] = []
        for index, member in enumerate(members, start=1):
            file_entry = _spdx_file(
                artifact_name=artifact_path.name,
                archive_kind=archive_kind,
                index=index,
                member=member,
            )
            artifact_files.append(file_entry)
            sha1_values.append(member[1])
            relationships.append(
                {
                    "spdxElementId": package_id,
                    "relatedSpdxElement": str(file_entry["SPDXID"]),
                    "relationshipType": "CONTAINS",
                }
            )
        _require(condition=bool(artifact_files), message=f"distribution archive is empty: {artifact_path.name}")
        files.extend(artifact_files)
        packages.append(
            _spdx_package(
                artifact_path=artifact_path,
                archive_kind=archive_kind,
                metadata=metadata,
                files=artifact_files,
                sha1_values=sha1_values,
            )
        )
        relationships.insert(
            len(packages) - 1,
            {
                "spdxElementId": "SPDXRef-DOCUMENT",
                "relatedSpdxElement": package_id,
                "relationshipType": "DESCRIBES",
            },
        )

    package_ids = [str(package["SPDXID"]) for package in packages]
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"{metadata.name}-{metadata.version}-release",
        "documentNamespace": f"{_REPOSITORY}/sbom/{metadata.name}/{metadata.version}/{source_commit}",
        "creationInfo": {
            "creators": ["Organization: pylopdf contributors", "Tool: pylopdf-font-sbom/1"],
            "created": created,
        },
        "documentDescribes": package_ids,
        "packages": packages,
        "files": files,
        "relationships": relationships,
    }


def main() -> None:
    """Parse CLI arguments and write a deterministic SPDX document."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-date-epoch", required=True, type=int)
    args = parser.parse_args()

    document = generate_font_sbom(
        args.dist_dir,
        source_commit=args.source_commit,
        source_date_epoch=args.source_date_epoch,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(f"{json.dumps(document, indent=2, ensure_ascii=False)}\n", encoding="utf-8")


if __name__ == "__main__":
    main()
