"""Small deterministic git fixture builders used by the test suite."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

ALICE_NAME = "Alice Example"
ALICE_EMAIL = "alice@example.com"
BOB_NAME = "Bob Example"
BOB_EMAIL = "bob@example.com"

REFERENCE = datetime(2024, 3, 14, 23, 0, tzinfo=timezone.utc)
BROAD_SINCE = datetime(2014, 1, 1, tzinfo=timezone.utc)


def _clean_environment(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C",
            "TZ": "UTC",
        }
    )
    if extra:
        env.update(extra)
    return env


def run_git(
    repository: Path,
    arguments: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="surrogateescape",
        env=_clean_environment(env),
        check=False,
        timeout=60,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"git {' '.join(arguments)} failed ({completed.returncode}): "
            f"{completed.stderr.strip()}"
        )
    return completed


class GitRepo:
    """A tiny, fully local repository with explicit commit dates."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def create(cls, parent: Path, name: str) -> "GitRepo":
        path = parent / name
        path.mkdir(parents=True, exist_ok=True)
        run_git(path, ["init", "-q", "-b", "main"])
        run_git(path, ["config", "user.name", "Fixture User"])
        run_git(path, ["config", "user.email", "fixture@example.com"])
        run_git(path, ["config", "commit.gpgsign", "false"])
        return cls(path)

    def write(self, relative_path: str, content: str = "") -> Path:
        target = self.path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def commit(
        self,
        when: datetime,
        message: str,
        *,
        author_name: str = ALICE_NAME,
        author_email: str = ALICE_EMAIL,
        allow_empty: bool = True,
    ) -> None:
        iso_date = when.astimezone(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S+00:00"
        )
        env = {
            "GIT_AUTHOR_NAME": author_name,
            "GIT_AUTHOR_EMAIL": author_email,
            "GIT_COMMITTER_NAME": author_name,
            "GIT_COMMITTER_EMAIL": author_email,
            "GIT_AUTHOR_DATE": iso_date,
            "GIT_COMMITTER_DATE": iso_date,
        }
        run_git(self.path, ["add", "-A"])
        command = ["commit", "-q", "--no-gpg-sign"]
        if allow_empty:
            command.append("--allow-empty")
        command.extend(["--date", iso_date, "-m", message])
        run_git(self.path, command, env=env)

    def detach_head(self) -> None:
        run_git(self.path, ["checkout", "-q", "--detach", "HEAD"])

    def head(self) -> str:
        return run_git(self.path, ["rev-parse", "HEAD"]).stdout.strip()


def temporary_directory(case: unittest.TestCase) -> Path:
    """Create a mkdtemp directory and guarantee cleanup via the test case."""

    path = Path(tempfile.mkdtemp(prefix="gitstats-card-test-"))
    case.addCleanup(shutil.rmtree, path, True)
    return path


@dataclass(frozen=True)
class StandardRepositories:
    root: Path
    alpha: GitRepo
    beta: GitRepo


def build_standard_repositories(root: Path) -> StandardRepositories:
    """Build the shared two-repository fixture used across test modules.

    Final tracked files:
      alpha: app.py, util.py, web.js, README.md, Makefile, notes with spaces.md
      beta:  script.py, notes.txt

    Expected unfiltered totals with :data:`REFERENCE` and a wide window:
      8 commits, 6 active dates, current streak 5, longest streak 5.
    """

    alpha = GitRepo.create(root, "alpha")
    alpha.write("app.py", "print('alpha')\n")
    alpha.commit(datetime(2024, 2, 28, 10, 0, tzinfo=timezone.utc), "start alpha")
    alpha.write("util.py", "VALUE = 1\n")
    alpha.commit(datetime(2024, 3, 10, 9, 0, tzinfo=timezone.utc), "add util")
    alpha.write("web.js", "export const answer = 42;\n")
    alpha.commit(datetime(2024, 3, 11, 9, 0, tzinfo=timezone.utc), "add web")
    alpha.write("README.md", "# Alpha\n")
    alpha.commit(
        datetime(2024, 3, 12, 8, 0, tzinfo=timezone.utc),
        "document alpha",
        author_name=BOB_NAME,
        author_email=BOB_EMAIL,
    )
    alpha.write("Makefile", "test:\n\tpython -m unittest\n")
    alpha.commit(datetime(2024, 3, 13, 12, 0, tzinfo=timezone.utc), "add makefile")
    alpha.write("notes with spaces.md", "spaces are fine\n")
    alpha.commit(datetime(2024, 3, 14, 18, 0, tzinfo=timezone.utc), "add notes")

    beta = GitRepo.create(root, "beta")
    beta.write("script.py", "print('beta')\n")
    beta.commit(
        datetime(2024, 3, 12, 10, 0, tzinfo=timezone.utc),
        "start beta",
        author_name=BOB_NAME,
        author_email=BOB_EMAIL,
    )
    beta.write("notes.txt", "notes\n")
    beta.commit(datetime(2024, 3, 14, 11, 0, tzinfo=timezone.utc), "add beta notes")

    return StandardRepositories(root=root, alpha=alpha, beta=beta)
