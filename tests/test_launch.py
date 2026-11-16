"""Exercise the Studio launcher across changes and incomplete npm commands."""

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import uvicorn

from autocan.api import launch


@pytest.fixture
def frontend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)
    for name in ("NODE_ENV", "VITE_TEST"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path / "frontend"


def _checkout(frontend: Path) -> None:
    (frontend / "src").mkdir(parents=True)
    for name in ("package.json", "package-lock.json", "index.html", "src/main.tsx"):
        (frontend / name).write_text("initial")


class Npm:
    def __init__(self, frontend: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.frontend = frontend
        self.commands: list[str] = []
        self.fail: str | None = None
        executable = frontend.parent / "npm"
        executable.touch()
        monkeypatch.setattr(shutil, "which", lambda name: str(executable))
        monkeypatch.setattr(subprocess, "run", self.run)

    def run(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        step = command[1]
        self.commands.append(step)
        if step == "ci":
            directory = self.frontend / "node_modules"
            if directory.exists():
                shutil.rmtree(directory)
            (directory / ".bin").mkdir(parents=True)
            suffix = ".cmd" if sys.platform == "win32" else ""
            for name in ("vite", "tsc"):
                (directory / ".bin" / (name + suffix)).touch()
            (directory / ".package-lock.json").write_text("installed dependencies")
        else:
            (self.frontend / "dist" / "assets").mkdir(parents=True, exist_ok=True)
            (self.frontend / "dist" / "index.html").write_text("build")
            (self.frontend / "dist" / "assets" / "main.js").write_text("bundle")
        return subprocess.CompletedProcess(command, 1 if step == self.fail else 0)


def test_warm_launch_only_repeats_the_steps_invalidated_by_changes(
    frontend: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _checkout(frontend)
    npm = Npm(frontend, monkeypatch)
    assert launch.launch() == 0
    assert npm.commands == ["ci", "run"]

    npm.commands.clear()
    (frontend / "tsconfig.tsbuildinfo").touch()
    (frontend / "test-results").mkdir()
    assert launch.launch() == 0
    assert npm.commands == []

    (frontend / "src" / "main.tsx").write_text("changed")
    assert launch.launch() == 0
    assert npm.commands == ["run"]

    npm.commands.clear()
    (frontend / "package-lock.json").write_text("new lockfile")
    assert launch.launch() == 0
    assert npm.commands == ["ci", "run"]


@pytest.mark.parametrize("missing", ["dist/assets/main.js", "node_modules"])
def test_launch_recovers_missing_generated_files(
    frontend: Path, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    _checkout(frontend)
    npm = Npm(frontend, monkeypatch)
    assert launch.launch() == 0
    npm.commands.clear()
    path = frontend / missing
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    assert launch.launch() == 0
    assert npm.commands == (["ci", "run"] if missing == "node_modules" else ["run"])


@pytest.mark.parametrize("step", ["ci", "run"])
def test_partial_npm_failure_does_not_count_as_a_cached_success(
    frontend: Path, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    _checkout(frontend)
    npm = Npm(frontend, monkeypatch)
    npm.fail = step
    assert launch.launch() == 1
    npm.commands.clear()
    npm.fail = None
    assert launch.launch() == 0
    assert npm.commands == (["ci", "run"] if step == "ci" else ["run"])
