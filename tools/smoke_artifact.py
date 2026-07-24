"""Install a built distribution artifact and exercise the installed package."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

_SMOKE_TEXT = "pylopdf artifact smoke test"
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _require(*, condition: bool, message: str) -> None:
    """Raise a useful smoke-test failure instead of relying on optimized asserts."""
    if not condition:
        raise RuntimeError(message)


def _run_smoke() -> None:
    """Exercise create, save, open, extract, and render from the installed package."""
    import pylopdf  # noqa: PLC0415  # Import only after the artifact is installed.

    doc = pylopdf.Document()
    page = doc.new_page(width=200, height=200)
    page.insert_text((20, 40), _SMOKE_TEXT)
    data = doc.tobytes()
    doc.close()

    with pylopdf.open(stream=data) as reopened:
        _require(
            condition=reopened.page_count == 1,
            message="reopened document has the wrong page count",
        )
        _require(
            condition=_SMOKE_TEXT in reopened[0].get_text(),
            message="text extraction lost inserted text",
        )
        _require(
            condition=reopened[0].get_pixmap().tobytes().startswith(_PNG_SIGNATURE),
            message="rendering did not return PNG data",
        )

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "smoke.pdf"
            reopened.save(output)
            with pylopdf.open(output) as saved:
                _require(
                    condition=_SMOKE_TEXT in saved[0].get_text(),
                    message="saved file lost inserted text",
                )

    sys.stdout.write(f"artifact smoke test passed with pylopdf {pylopdf.__version__}\n")


def _install_and_spawn(artifact_dir: Path, pattern: str) -> None:
    """Install the single matching artifact and run smoke checks in a clean cwd."""
    artifacts = sorted(artifact_dir.glob(pattern))
    if len(artifacts) != 1:
        msg = f"expected exactly one {pattern!r} artifact in {artifact_dir}, found {artifacts}"
        raise RuntimeError(msg)

    artifact = artifacts[0].resolve()
    subprocess.run(  # noqa: S603  # Execute the current interpreter with one CI-built artifact.
        [sys.executable, "-m", "pip", "install", "--force-reinstall", str(artifact)],
        check=True,
    )
    with tempfile.TemporaryDirectory() as directory:
        subprocess.run(  # noqa: S603  # Re-enter this checked-in script with the current interpreter.
            [sys.executable, str(Path(__file__).resolve()), "--run"],
            check=True,
            cwd=directory,
        )


def main() -> None:
    """Parse CLI arguments and install or exercise an artifact."""
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact_dir", nargs="?", type=Path)
    parser.add_argument("--pattern", default="*.whl")
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()

    if args.run:
        _run_smoke()
        return
    if args.artifact_dir is None:
        parser.error("artifact_dir is required unless --run is used")
    _install_and_spawn(args.artifact_dir, args.pattern)


if __name__ == "__main__":
    main()
