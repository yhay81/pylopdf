from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

_COMMIT = "0123456789abcdef0123456789abcdef01234567"
_GENERATOR = Path(__file__).resolve().parents[1] / "tools" / "generate_font_sbom.py"
_METADATA = b"""\
Metadata-Version: 2.4
Name: pylopdf-fonts-test
Version: 0.1.0
Summary: Test fonts for pylopdf

"""


def _write_distributions(dist_dir: Path) -> None:
    dist_dir.mkdir()
    wheel = dist_dir / "pylopdf_fonts_test-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("pylopdf_fonts_test/__init__.py", '__version__ = "0.1.0"\n')
        archive.writestr("pylopdf_fonts_test/font.otf", b"font payload")
        archive.writestr("pylopdf_fonts_test-0.1.0.dist-info/METADATA", _METADATA)

    sdist = dist_dir / "pylopdf_fonts_test-0.1.0.tar.gz"
    with tarfile.open(sdist, "w:gz") as archive:
        for name, payload in (
            ("pylopdf_fonts_test-0.1.0/PKG-INFO", _METADATA),
            ("pylopdf_fonts_test-0.1.0/src/pylopdf_fonts_test/font.otf", b"font payload"),
        ):
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))


def _sha1(payload: bytes) -> str:
    return hashlib.sha1(payload, usedforsecurity=False).hexdigest()


def _generate(dist_dir: Path, output: Path, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603  # Execute the checked-in generator with fixed test arguments.
        [
            sys.executable,
            str(_GENERATOR),
            "--dist-dir",
            str(dist_dir),
            "--output",
            str(output),
            "--source-commit",
            _COMMIT,
            "--source-date-epoch",
            "0",
        ],
        check=check,
        capture_output=True,
        text=True,
    )


def test_font_sbom_describes_both_archives_and_every_member(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    _write_distributions(dist_dir)
    output = tmp_path / "sbom.spdx.json"

    _generate(dist_dir, output)
    repeated_output = tmp_path / "repeated.spdx.json"
    _generate(dist_dir, repeated_output)
    assert repeated_output.read_bytes() == output.read_bytes()

    document = json.loads(output.read_text(encoding="utf-8"))

    assert document["spdxVersion"] == "SPDX-2.3"
    assert document["name"] == "pylopdf-fonts-test-0.1.0-release"
    assert document["creationInfo"]["created"] == "1970-01-01T00:00:00Z"
    assert document["documentNamespace"].endswith(f"/pylopdf-fonts-test/0.1.0/{_COMMIT}")
    assert document["documentDescribes"] == ["SPDXRef-Package-Wheel", "SPDXRef-Package-Sdist"]

    packages = document["packages"]
    assert len(packages) == 2
    assert {package["packageFileName"] for package in packages} == {
        "pylopdf_fonts_test-0.1.0-py3-none-any.whl",
        "pylopdf_fonts_test-0.1.0.tar.gz",
    }
    for package in packages:
        assert package["name"] == "pylopdf-fonts-test"
        assert package["versionInfo"] == "0.1.0"
        assert package["filesAnalyzed"] is True
        assert package["licenseDeclared"] == "OFL-1.1"
        assert package["externalRefs"][0]["referenceLocator"] == "pkg:pypi/pylopdf-fonts-test@0.1.0"
        assert len(package["checksums"][0]["checksumValue"]) == 64

    files = document["files"]
    assert len(files) == 5
    assert len({file["SPDXID"] for file in files}) == 5
    assert all(file["checksums"][0]["algorithm"] == "SHA1" for file in files)
    assert all(file["checksums"][1]["algorithm"] == "SHA256" for file in files)

    wheel = packages[0]
    wheel_sha1 = sorted(
        [
            _sha1(b'__version__ = "0.1.0"\n'),
            _sha1(b"font payload"),
            _sha1(_METADATA),
        ]
    )
    expected_code = _sha1("".join(wheel_sha1).encode())
    assert wheel["packageVerificationCode"]["packageVerificationCodeValue"] == expected_code

    relationships = document["relationships"]
    assert sum(item["relationshipType"] == "DESCRIBES" for item in relationships) == 2
    assert sum(item["relationshipType"] == "CONTAINS" for item in relationships) == 5
    json.dumps(document)


def test_font_sbom_rejects_unsafe_archive_members(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    _write_distributions(dist_dir)
    wheel = next(dist_dir.glob("*.whl"))
    with zipfile.ZipFile(wheel, "a") as archive:
        archive.writestr("../outside.otf", b"unsafe")

    result = _generate(dist_dir, tmp_path / "sbom.spdx.json", check=False)

    assert result.returncode != 0
    assert "unsafe member path" in result.stderr
