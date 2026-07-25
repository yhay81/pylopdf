"""Bundle a pylopdf PyEmscripten wheel into a Cloudflare Python Worker."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_PYEMSCRIPTEN_TAG = "cp310-abi3-pyemscripten_2025_0_wasm32"
_WORKERS_PY_VERSION = "1.15.0"
_WRANGLER_VERSION = "4.114.0"
_COMPATIBILITY_DATE = "2026-07-26"
_EXAMPLE_REQUIREMENT = "pylopdf>=0.11,<0.12"
_EXAMPLE_ROOT = Path(__file__).resolve().parents[1] / "examples" / "cloudflare-worker"
_UPLOAD_RE = re.compile(r"Total Upload:\s+([0-9.]+)\s+KiB\s+/\s+gzip:\s+([0-9.]+)\s+KiB")


def _require_command(name: str) -> str:
    command = shutil.which(name)
    if command is None:
        msg = f"required command is unavailable: {name}"
        raise RuntimeError(msg)
    return command


def _write_project(root: Path, requirement: str) -> None:
    shutil.copytree(_EXAMPLE_ROOT / "src", root / "src")
    shutil.copy2(_EXAMPLE_ROOT / "wrangler.jsonc", root / "wrangler.jsonc")
    project = (_EXAMPLE_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    expected = json.dumps(_EXAMPLE_REQUIREMENT)
    replacement = json.dumps(requirement)
    if project.count(expected) != 1:
        msg = f"expected one {expected} dependency in the Cloudflare example"
        raise RuntimeError(msg)
    (root / "pyproject.toml").write_text(
        project.replace(expected, replacement),
        encoding="utf-8",
    )


def _tree_size(root: Path) -> tuple[int, int]:
    files = [path for path in root.rglob("*") if path.is_file()]
    return sum(path.stat().st_size for path in files), len(files)


def _verify_vendored_wheel(root: Path) -> Path:
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
    return extensions[0]


def _run_attempt(root: Path, requirement: str) -> dict[str, object]:
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
    extension = _verify_vendored_wheel(root)
    vendored_bytes, vendored_files = _tree_size(root / "python_modules")
    output = root / "cloudflare-dist"
    completed = subprocess.run(  # noqa: S603
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
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    sys.stdout.write(completed.stdout)
    sys.stderr.write(completed.stderr)
    completed.check_returncode()
    if not output.is_dir() or not any(output.iterdir()):
        msg = "Wrangler dry run did not produce a bundle"
        raise RuntimeError(msg)
    match = _UPLOAD_RE.search(f"{completed.stdout}\n{completed.stderr}")
    if match is None:
        msg = "Wrangler output did not report total and gzip upload sizes"
        raise RuntimeError(msg)
    output_bytes, output_files = _tree_size(output)
    return {
        "schema": 1,
        "workers_py": _WORKERS_PY_VERSION,
        "wrangler": _WRANGLER_VERSION,
        "compatibility_date": _COMPATIBILITY_DATE,
        "vendored_bytes": vendored_bytes,
        "vendored_files": vendored_files,
        "extension_bytes": extension.stat().st_size,
        "dry_run_output_bytes": output_bytes,
        "dry_run_output_files": output_files,
        "total_upload_bytes": round(float(match.group(1)) * 1024),
        "gzip_upload_bytes": round(float(match.group(2)) * 1024),
    }


def smoke_cloudflare(
    requirement: str,
    *,
    attempts: int,
    retry_delay: float,
) -> dict[str, object]:
    """Resolve, vendor, and dry-run bundle a package requirement."""
    last_error: subprocess.CalledProcessError | RuntimeError | None = None
    for attempt in range(1, attempts + 1):
        with tempfile.TemporaryDirectory(prefix="pylopdf-cloudflare-") as directory:
            try:
                metrics = _run_attempt(Path(directory), requirement)
            except (subprocess.CalledProcessError, RuntimeError) as error:
                last_error = error
            else:
                sys.stdout.write(
                    "Cloudflare Workers smoke test passed "
                    f"with workers-py {_WORKERS_PY_VERSION} and Wrangler {_WRANGLER_VERSION}\n"
                )
                return metrics
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
    parser.add_argument("--metrics-output", type=Path)
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
    metrics = smoke_cloudflare(
        requirement,
        attempts=args.attempts,
        retry_delay=args.retry_delay,
    )
    sys.stdout.write(f"Cloudflare bundle metrics: {json.dumps(metrics, sort_keys=True)}\n")
    if args.metrics_output is not None:
        args.metrics_output.parent.mkdir(parents=True, exist_ok=True)
        args.metrics_output.write_text(
            f"{json.dumps(metrics, sort_keys=True, separators=(',', ':'))}\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
