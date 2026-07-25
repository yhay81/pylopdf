"""Bundle a pylopdf PyEmscripten wheel into a Cloudflare Python Worker."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_PYEMSCRIPTEN_TAG = "cp310-abi3-pyemscripten_2025_0_wasm32"
_WORKERS_PY_VERSION = "1.15.0"
_WRANGLER_VERSION = "4.114.0"


def _require_command(name: str) -> str:
    command = shutil.which(name)
    if command is None:
        msg = f"required command is unavailable: {name}"
        raise RuntimeError(msg)
    return command


def _write_project(root: Path, requirement: str) -> None:
    source = root / "src"
    source.mkdir(parents=True)
    dependencies = json.dumps([requirement])
    (root / "pyproject.toml").write_text(
        f"""\
[project]
name = "pylopdf-cloudflare-smoke"
version = "0.0.0"
requires-python = ">=3.13"
dependencies = {dependencies}
""",
        encoding="utf-8",
    )
    (root / "wrangler.jsonc").write_text(
        """\
{
  "name": "pylopdf-cloudflare-smoke",
  "main": "src/entry.py",
  "compatibility_date": "2026-07-26",
  "compatibility_flags": ["python_workers"]
}
""",
        encoding="utf-8",
    )
    (source / "entry.py").write_text(
        """\
import pylopdf
from workers import Response, WorkerEntrypoint


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        document = pylopdf.Document()
        document.new_page()
        return Response(f"pylopdf {pylopdf.__version__}: {document.page_count} page")
""",
        encoding="utf-8",
    )


def _verify_vendored_wheel(root: Path) -> None:
    extensions = list((root / "python_modules" / "pylopdf").glob("pylopdf_core*.so"))
    if len(extensions) != 1:
        msg = f"expected one vendored pylopdf extension, found {extensions}"
        raise RuntimeError(msg)
    wheel_metadata = list((root / "python_modules").glob("pylopdf-*.dist-info/WHEEL"))
    if len(wheel_metadata) != 1:
        msg = f"expected one vendored pylopdf WHEEL file, found {wheel_metadata}"
        raise RuntimeError(msg)
    metadata = wheel_metadata[0].read_text(encoding="utf-8")
    if f"Tag: {_PYEMSCRIPTEN_TAG}" not in metadata:
        msg = f"vendored wheel metadata does not contain Tag: {_PYEMSCRIPTEN_TAG}"
        raise RuntimeError(msg)


def _run_attempt(root: Path, requirement: str) -> None:
    _write_project(root, requirement)
    uvx = _require_command("uvx")
    npx = _require_command("npx")
    subprocess.run(  # noqa: S603
        [
            uvx,
            "--from",
            f"workers-py=={_WORKERS_PY_VERSION}",
            "pywrangler",
            "sync",
            "--force",
            "--upgrade",
        ],
        cwd=root,
        check=True,
    )
    _verify_vendored_wheel(root)
    output = root / "cloudflare-dist"
    subprocess.run(  # noqa: S603
        [
            npx,
            "--yes",
            f"wrangler@{_WRANGLER_VERSION}",
            "deploy",
            "--dry-run",
            "--outdir",
            str(output),
        ],
        cwd=root,
        check=True,
    )
    if not output.is_dir() or not any(output.iterdir()):
        msg = "Wrangler dry run did not produce a bundle"
        raise RuntimeError(msg)


def smoke_cloudflare(requirement: str, *, attempts: int, retry_delay: float) -> None:
    """Resolve, vendor, and dry-run bundle a package requirement."""
    last_error: subprocess.CalledProcessError | RuntimeError | None = None
    for attempt in range(1, attempts + 1):
        with tempfile.TemporaryDirectory(prefix="pylopdf-cloudflare-") as directory:
            try:
                _run_attempt(Path(directory), requirement)
            except (subprocess.CalledProcessError, RuntimeError) as error:
                last_error = error
            else:
                sys.stdout.write(
                    "Cloudflare Workers smoke test passed "
                    f"with workers-py {_WORKERS_PY_VERSION} and Wrangler {_WRANGLER_VERSION}\n"
                )
                return
        if attempt < attempts:
            sys.stdout.write(
                f"Cloudflare smoke attempt {attempt}/{attempts} failed; retrying in {retry_delay:g} seconds\n"
            )
            time.sleep(retry_delay)
    if last_error is None:
        msg = "Cloudflare smoke test did not run"
        raise RuntimeError(msg)
    raise last_error


def main() -> None:
    """Parse command-line arguments and run the Cloudflare bundle smoke test."""
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--wheel", type=Path)
    source.add_argument("--requirement")
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--retry-delay", type=float, default=15.0)
    args = parser.parse_args()
    if args.attempts < 1:
        parser.error("--attempts must be at least 1")
    if args.retry_delay < 0:
        parser.error("--retry-delay must be non-negative")

    if args.wheel is not None:
        wheel = args.wheel.resolve()
        if not wheel.is_file():
            parser.error(f"wheel does not exist: {wheel}")
        if not wheel.name.endswith(f"-{_PYEMSCRIPTEN_TAG}.whl"):
            parser.error(f"wheel does not use the expected {_PYEMSCRIPTEN_TAG} tag: {wheel.name}")
        requirement = f"pylopdf @ {wheel.as_uri()}"
    else:
        if args.requirement is None:
            msg = "a wheel or requirement is required"
            raise RuntimeError(msg)
        requirement = args.requirement
    smoke_cloudflare(requirement, attempts=args.attempts, retry_delay=args.retry_delay)


if __name__ == "__main__":
    main()
