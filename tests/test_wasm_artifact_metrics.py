from __future__ import annotations

import zipfile
from collections.abc import Callable
from pathlib import Path
from runpy import run_path
from typing import Any, cast

import pytest

_METRICS_TOOL = run_path(str(Path(__file__).parents[1] / "tools" / "wasm_artifact_metrics.py"))
collect_metrics = cast("Callable[[Path], dict[str, Any]]", _METRICS_TOOL["collect_metrics"])
wasm_section_metrics = cast(
    "Callable[[bytes], dict[str, dict[str, int]]]",
    _METRICS_TOOL["wasm_section_metrics"],
)

_EMPTY_WASM = b"\0asm\x01\0\0\0"


def test_wasm_section_metrics_aggregates_standard_and_custom_sections() -> None:
    wasm = _EMPTY_WASM + b"\x01\x01\x00" + b"\x0a\x01\x00" + b"\x00\x05\x04name"

    assert wasm_section_metrics(wasm) == {
        "code": {"count": 1, "encoded_bytes": 3, "payload_bytes": 1},
        "custom:name": {"count": 1, "encoded_bytes": 7, "payload_bytes": 5},
        "type": {"count": 1, "encoded_bytes": 3, "payload_bytes": 1},
    }


@pytest.mark.parametrize("data", [b"", b"\0asm\0\0\0\0", _EMPTY_WASM + b"\x01\x02\x00"])
def test_wasm_section_metrics_rejects_invalid_input(data: bytes) -> None:
    with pytest.raises(ValueError, match="WebAssembly"):
        wasm_section_metrics(data)


def test_collect_metrics_groups_wheel_payload(tmp_path: Path) -> None:
    wheel = tmp_path / "pylopdf-test.whl"
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("pylopdf/pylopdf_core.abi3.so", _EMPTY_WASM + b"\x0a\x01\x00")
        archive.writestr("pylopdf/__init__.py", "VERSION = 'test'\n")
        archive.writestr("pylopdf-0.dist-info/METADATA", "Name: pylopdf\n")
        archive.writestr("pylopdf-0.dist-info/licenses/LICENSE", "MIT\n")
        archive.writestr("pylopdf-0.dist-info/sboms/core.json", "{}\n")

    metrics = collect_metrics(wheel)

    assert metrics["wheel"]["files"] == 5
    assert metrics["extension"]["uncompressed_bytes"] == 11
    assert metrics["extension"]["sections"]["code"]["payload_bytes"] == 1
    assert metrics["groups"]["extension"]["files"] == 1
    assert metrics["groups"]["python"]["files"] == 1
    assert metrics["groups"]["metadata"]["files"] == 1
    assert metrics["groups"]["licenses"]["files"] == 1
    assert metrics["groups"]["sbom"]["files"] == 1
