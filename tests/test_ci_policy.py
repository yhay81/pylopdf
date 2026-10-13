"""Guard GitHub-hosted execution and explicit workload deadlines."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
import yaml  # type: ignore[import-untyped]

_WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
_HOSTED_RUNNERS = {
    "ubuntu-latest",
    "ubuntu-24.04-arm",
    "macos-latest",
    "macos-15-intel",
    "windows-latest",
}


def _workflow(path: Path) -> dict[str, object]:
    return cast("dict[str, object]", yaml.safe_load(path.read_text(encoding="utf-8")))


def _routes(job: dict[str, object]) -> list[tuple[object, object]]:
    runner = job["runs-on"]
    timeout = job["timeout-minutes"]
    if isinstance(runner, str) and runner.startswith("${{"):
        strategy = cast("dict[str, object]", job["strategy"])
        matrix = cast("dict[str, object]", strategy["matrix"])
        if runner == "${{ matrix.os }}":
            assert isinstance(timeout, int)
            return [(entry, timeout) for entry in cast("list[str]", matrix["os"])]
        if runner == "${{ matrix.runner }}":
            entries = cast("list[dict[str, object]]", matrix["include"])
            if timeout == "${{ matrix.timeout }}":
                return [(entry["runner"], entry["timeout"]) for entry in entries]
            return [(entry["runner"], timeout) for entry in entries]
        if runner == "${{ matrix.platform.runner }}":
            entries = cast("list[dict[str, object]]", matrix["platform"])
            return [(entry["runner"], timeout) for entry in entries]
        pytest.fail(f"Unsupported runner expression: {runner}")
    return [(runner, timeout)]


@pytest.mark.parametrize("workflow_path", sorted(_WORKFLOWS.glob("*.yml")), ids=lambda path: path.name)
def test_jobs_run_only_on_github_hosted_runners(workflow_path: Path) -> None:
    jobs = cast("dict[str, dict[str, object]]", _workflow(workflow_path)["jobs"])
    for name, job in jobs.items():
        for runner, _timeout in _routes(job):
            assert isinstance(runner, str), (workflow_path.name, name, runner)
            assert runner in _HOSTED_RUNNERS, (workflow_path.name, name, runner)


@pytest.mark.parametrize("workflow_path", sorted(_WORKFLOWS.glob("*.yml")), ids=lambda path: path.name)
def test_jobs_define_explicit_positive_timeouts(workflow_path: Path) -> None:
    jobs = cast("dict[str, dict[str, object]]", _workflow(workflow_path)["jobs"])
    for name, job in jobs.items():
        for _runner, timeout in _routes(job):
            assert type(timeout) is int, (workflow_path.name, name)
            assert timeout > 0, (workflow_path.name, name)


def test_native_ci_builds_on_all_three_operating_systems() -> None:
    jobs = cast("dict[str, dict[str, object]]", _workflow(_WORKFLOWS / "ci.yml")["jobs"])
    assert {runner for runner, _timeout in _routes(jobs["test"])} == {
        "ubuntu-latest",
        "macos-latest",
        "windows-latest",
    }


def test_fuzz_deadline_leaves_time_for_build_and_reproducer_upload() -> None:
    path = _WORKFLOWS / "fuzz.yml"
    jobs = cast("dict[str, dict[str, object]]", _workflow(path)["jobs"])
    timeout = cast("int", jobs["api"]["timeout-minutes"])
    assert "-max_total_time=600" in path.read_text(encoding="utf-8")
    assert timeout * 60 > 600 + 60
