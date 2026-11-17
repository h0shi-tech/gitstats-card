"""Discover local git repositories and read the data needed by the card.

This module is the only place in :mod:`gitstats_card` that talks to git.  It
never invokes a shell, always uses ``git -C <path>``, and keeps commands bounded
with a timeout.  Aggregation lives in :mod:`gitstats_card.stats`.
"""

from __future__ import annotations

import fnmatch
import math
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

GIT_TIMEOUT_SECONDS = 60
"""Maximum wall-clock time for one git subprocess."""

# A commit exactly on a window boundary can be interpreted differently by
# different git versions.  Ask git for a one-second superset; the pure stats
# layer applies the exact inclusive window.
_QUERY_SLOP_SECONDS = 1


class GitStatsError(Exception):
    """Base class for clean, expected CLI failures."""


class GitUnavailableError(GitStatsError):
    """Raised when the ``git`` executable cannot be found."""


class InputPathError(GitStatsError):
    """Raised when a user-supplied discovery root is missing or not a directory."""


class RepositoryError(GitStatsError):
    """Raised when git cannot read a discovered repository."""


@dataclass(frozen=True, slots=True)
class Commit:
    """One commit's committer timestamp and author identity."""

    timestamp: int
    author_name: str
    author_email: str


@dataclass(frozen=True, slots=True)
class Repository:
    """A repository snapshot limited to the requested time window."""

    path: Path
    commits: tuple[Commit, ...]
    files: tuple[str, ...]

    @property
    def name(self) -> str:
        """The display name for the repository (its directory name)."""

        return self.path.name or str(self.path)


@dataclass(frozen=True, slots=True)
class CollectionResult:
    """Repositories successfully read, plus non-fatal problems."""

    repositories: tuple[Repository, ...]
    discovered: int
    warnings: tuple[str, ...]


def ensure_git_available() -> None:
    """Raise :class:`GitUnavailableError` unless git is on ``PATH``.

    Kept as a small public function so the CLI can fail before doing any work
    and so callers (and tests) can exercise the missing-git path directly.
    """

    if shutil.which("git") is None:
        raise GitUnavailableError(
            "git executable not found; install git and make sure it is on PATH"
        )


def _git_environment() -> dict[str, str]:
    env = os.environ.copy()
    # Do not let a caller's GIT_DIR/GIT_WORK_TREE redirect ``-C`` at a
    # different repository.
    for variable in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR"):
        env.pop(variable, None)
    env.update(
        {
            "GIT_PAGER": "cat",
            "PAGER": "cat",
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C",
            "LANG": "C",
            "TZ": "UTC",
        }
    )
    return env


def _error_detail(stderr: str) -> str:
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    return lines[-1] if lines else "no diagnostic output"


def _run_git(
    repository: Path,
    arguments: Sequence[str],
    *,
    allow_failure: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run git without a shell, returning captured text output.

    Output is decoded as UTF-8 with the ``surrogateescape`` error handler so
    unusual (non-UTF-8) tracked file names do not crash the collector.  NUL
    delimiters are used by callers that parse file lists or commit records.
    """

    command = ["git", "-C", str(repository), *arguments]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            timeout=GIT_TIMEOUT_SECONDS,
            env=_git_environment(),
            check=False,
        )
    except FileNotFoundError as exc:
        raise GitUnavailableError(
            "git executable not found; install git and make sure it is on PATH"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RepositoryError(
            f"git command timed out after {GIT_TIMEOUT_SECONDS} seconds"
        ) from exc

    if completed.returncode != 0 and not allow_failure:
        raise RepositoryError(
            f"git {arguments[0]} failed with exit code {completed.returncode}: "
            f"{_error_detail(completed.stderr)}"
        )
    return completed


def _standardise_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _matches_exclude(repository: Path, root: Path, patterns: Sequence[str]) -> bool:
    try:
        relative = repository.relative_to(root).as_posix()
    except ValueError:
        relative = str(repository)
    candidates = (repository.name, relative, str(repository))
    return any(
        fnmatch.fnmatchcase(candidate, pattern)
        for pattern in patterns
        for candidate in candidates
    )


def discover_repositories(
    paths: Sequence[str | os.PathLike[str]],
    *,
    exclude: Sequence[str] = (),
) -> tuple[Path, ...]:
    """Find top-level git working trees below each supplied path.

    A path may itself be a repository or a directory containing repositories.
    Discovery stops descending when it finds a repository, so nested checkouts
    inside the first one are not double-counted.  Patterns in ``exclude`` are
    matched case-sensitively against the repository directory name, its
    relative path from the discovery root, and its absolute path.

    Raises:
        InputPathError: If a root does not exist or is not a directory.
    """

    if not paths:
        paths = (Path.cwd(),)

    roots: list[Path] = []
    for raw_path in paths:
        candidate = Path(raw_path).expanduser()
        try:
            candidate = candidate.resolve(strict=True)
        except FileNotFoundError as exc:
            raise InputPathError(f"path does not exist: {raw_path}") from exc
        except OSError as exc:
            raise InputPathError(f"cannot resolve path {raw_path}: {exc}") from exc
        if not candidate.is_dir():
            raise InputPathError(f"path is not a directory: {raw_path}")
        roots.append(candidate)

    found: set[Path] = set()
    for root in roots:
        if (root / ".git").exists():
            if not _matches_exclude(root, root, exclude):
                found.add(root)
            continue

        for directory, dirnames, _filenames in os.walk(
            root, topdown=True, followlinks=False
        ):
            current = Path(directory)
            # Never inspect git's own directory, and walk in a stable order.
            dirnames[:] = [name for name in dirnames if name != ".git"]

            if (current / ".git").exists():
                if not _matches_exclude(current, root, exclude):
                    found.add(current)
                dirnames[:] = []
                continue

            # Sorting keeps traversal deterministic even though the final
            # result is sorted again below.
            dirnames.sort()

    return tuple(sorted(found, key=lambda path: path.as_posix()))


def _window_bounds(since: datetime, until: datetime) -> tuple[int, int]:
    since_utc = _standardise_datetime(since)
    until_utc = _standardise_datetime(until)
    if since_utc > until_utc:
        raise ValueError("window start must not be after window end")
    start = math.floor(since_utc.timestamp()) - _QUERY_SLOP_SECONDS
    end = math.ceil(until_utc.timestamp()) + _QUERY_SLOP_SECONDS
    return max(0, start), end


def _parse_commit_records(output: str) -> tuple[Commit, ...]:
    """Parse ``%ct%x00%an%x00%ae`` records produced with ``git log -z``."""

    fields = output.split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    if len(fields) % 3 != 0:
        raise RepositoryError("could not parse git log output")
    commits: list[Commit] = []
    for index in range(0, len(fields), 3):
        timestamp_text, author_name, author_email = fields[index : index + 3]
        try:
            timestamp = int(timestamp_text)
        except ValueError as exc:
            raise RepositoryError("git returned a commit with an invalid date") from exc
        commits.append(
            Commit(
                timestamp=timestamp,
                author_name=author_name,
                author_email=author_email,
            )
        )
    return tuple(commits)


def _parse_tracked_files(output: str) -> tuple[str, ...]:
    return tuple(item for item in output.split("\0") if item)


def read_repository(
    path: str | os.PathLike[str],
    *,
    since: datetime,
    until: datetime,
) -> Repository:
    """Read one repository's commits in a window and its tracked files.

    Empty/unborn repositories are valid and produce an empty snapshot.  A
    directory that exists but is not a readable git repository raises
    :class:`RepositoryError`.
    """

    ensure_git_available()
    repository = Path(path).expanduser()
    try:
        repository = repository.resolve(strict=True)
    except FileNotFoundError as exc:
        raise RepositoryError(f"repository path does not exist: {path}") from exc
    except OSError as exc:
        raise RepositoryError(f"cannot resolve repository path {path}: {exc}") from exc
    if not repository.is_dir():
        raise RepositoryError(f"repository path is not a directory: {path}")

    # Validate the repository before treating an unborn HEAD as an empty repo.
    git_dir = _run_git(
        repository, ["rev-parse", "--git-dir"], allow_failure=True
    )
    if git_dir.returncode != 0:
        raise RepositoryError(
            f"{repository} is not a readable git repository: "
            f"{_error_detail(git_dir.stderr)}"
        )

    head = _run_git(
        repository, ["rev-parse", "--verify", "--quiet", "HEAD"], allow_failure=True
    )
    if head.returncode == 0:
        window_start, window_end = _window_bounds(since, until)
        log = _run_git(
            repository,
            [
                "log",
                "-z",
                "--no-show-signature",
                "--no-decorate",
                f"--since=@{window_start}",
                f"--until=@{window_end}",
                "--format=%ct%x00%an%x00%ae",
                "HEAD",
                "--",
            ],
        )
        commits = _parse_commit_records(log.stdout)
    elif head.returncode == 1:
        # A newly initialised repository with no commits.  It is still a valid
        # repository, and may already have tracked files in its index.
        commits = ()
    else:
        raise RepositoryError(
            f"could not inspect HEAD in {repository}: {_error_detail(head.stderr)}"
        )

    files_result = _run_git(repository, ["ls-files", "-z", "--cached"])
    files = _parse_tracked_files(files_result.stdout)
    return Repository(path=repository, commits=commits, files=files)


def collect_repositories(
    paths: Sequence[str | os.PathLike[str]],
    *,
    since: datetime,
    until: datetime,
    exclude: Sequence[str] = (),
) -> CollectionResult:
    """Discover and read repositories, skipping unreadable ones with warnings.

    The returned commits are a small superset of the requested inclusive
    window.  :func:`gitstats_card.stats.build_aggregate` applies the exact
    filter, name matching, and calendar aggregation.
    """

    ensure_git_available()
    repositories = discover_repositories(paths, exclude=exclude)
    snapshots: list[Repository] = []
    warnings: list[str] = []

    for repository in repositories:
        try:
            snapshots.append(read_repository(repository, since=since, until=until))
        except RepositoryError as exc:
            warnings.append(f"skipping {repository}: {exc}")

    return CollectionResult(
        repositories=tuple(snapshots),
        discovered=len(repositories),
        warnings=tuple(warnings),
    )
