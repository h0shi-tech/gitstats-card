"""Pure aggregation of collected git data into the card's data model.

There is no filesystem, network, or clock access here.  Callers provide a
reference timestamp and repository snapshots, which makes the calculations
straightforward to test and keeps the SVG renderer purely presentational.

Calendar rules
--------------
* All dates and hours are UTC.
* A commit's date is derived from its committer timestamp.
* The rolling window is inclusive at both ends: commits with timestamps in
  ``[generated_at - days, generated_at]`` are counted.
* The current streak is the number of consecutive UTC dates with at least one
  commit, ending at the reference date.  Because the current UTC day is not over
  while the card is generated, the chain may end yesterday when there is no
  commit today; it is ``0`` only when neither today nor yesterday has a commit.
  The longest streak is the longest consecutive run anywhere in the selected
  window.
* The month strip always contains the 12 UTC calendar months ending with the
  reference month, including months with zero commits.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any, Sequence

from .collect import Commit, Repository

SCHEMA_VERSION = 1
STRIP_MONTHS = 12
TOP_REPOSITORIES = 5
UNKNOWN_LANGUAGE = "Unknown"

# Extension -> display name.  Keep this intentionally opinionated but stable:
# changing the table changes the card's language mix, so it is part of the
# public data schema in practice.
LANGUAGES_BY_EXTENSION: dict[str, str] = {
    "asm": "Assembly",
    "astro": "Astro",
    "bash": "Shell",
    "bat": "Batchfile",
    "c": "C",
    "c++": "C++",
    "cc": "C++",
    "cfg": "Config",
    "clj": "Clojure",
    "cljc": "Clojure",
    "cljs": "Clojure",
    "cmd": "Batchfile",
    "conf": "Config",
    "cpp": "C++",
    "cs": "C#",
    "css": "CSS",
    "cxx": "C++",
    "dart": "Dart",
    "ex": "Elixir",
    "exs": "Elixir",
    "fish": "Shell",
    "fs": "F#",
    "fsi": "F#",
    "fsx": "F#",
    "go": "Go",
    "gql": "GraphQL",
    "graphql": "GraphQL",
    "groovy": "Groovy",
    "h": "C",
    "h++": "C++",
    "hh": "C++",
    "hpp": "C++",
    "hrl": "Erlang",
    "hs": "Haskell",
    "htm": "HTML",
    "html": "HTML",
    "hxx": "C++",
    "ini": "INI",
    "ipynb": "Jupyter Notebook",
    "java": "Java",
    "jl": "Julia",
    "js": "JavaScript",
    "json": "JSON",
    "jsonc": "JSON with Comments",
    "jsx": "JavaScript",
    "kt": "Kotlin",
    "kts": "Kotlin",
    "less": "Less",
    "lhs": "Haskell",
    "lua": "Lua",
    "m": "Objective-C",
    "markdown": "Markdown",
    "md": "Markdown",
    "mjs": "JavaScript",
    "ml": "OCaml",
    "mli": "OCaml",
    "mm": "Objective-C++",
    "mts": "TypeScript",
    "nim": "Nim",
    "org": "Org",
    "php": "PHP",
    "pl": "Perl",
    "pm": "Perl",
    "proto": "Protocol Buffers",
    "ps1": "PowerShell",
    "psm1": "PowerShell",
    "py": "Python",
    "pyi": "Python",
    "r": "R",
    "rb": "Ruby",
    "rs": "Rust",
    "rst": "reStructuredText",
    "sass": "SCSS",
    "scala": "Scala",
    "scss": "SCSS",
    "sh": "Shell",
    "sol": "Solidity",
    "sql": "SQL",
    "svelte": "Svelte",
    "swift": "Swift",
    "tex": "LaTeX",
    "toml": "TOML",
    "ts": "TypeScript",
    "tsx": "TypeScript",
    "txt": "Text",
    "vue": "Vue",
    "xml": "XML",
    "yaml": "YAML",
    "yml": "YAML",
    "zig": "Zig",
    "zsh": "Shell",
}


def language_for_filename(filename: str) -> str:
    """Return the language for a tracked path based on its final extension.

    Extensionless files, dotfiles without a second dot, and unrecognised
    extensions all fall into the :data:`UNKNOWN_LANGUAGE` bucket.
    """

    name = filename.rsplit("/", 1)[-1]
    if "." not in name:
        return UNKNOWN_LANGUAGE
    extension = name.rsplit(".", 1)[1].lower()
    if not extension:
        return UNKNOWN_LANGUAGE
    return LANGUAGES_BY_EXTENSION.get(extension, UNKNOWN_LANGUAGE)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _format_instant(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _author_matches(commit: Commit, author_filter: str | None) -> bool:
    if not author_filter:
        return True
    needle = author_filter.casefold()
    return (
        needle in commit.author_name.casefold()
        or needle in commit.author_email.casefold()
    )


def _streaks(active_dates: set[date], reference_date: date) -> tuple[int, int]:
    """Return ``(current_streak, longest_streak)`` in UTC calendar days."""

    if not active_dates:
        return 0, 0

    ordered = sorted(active_dates)
    longest = 1
    run = 1
    for previous, current in zip(ordered, ordered[1:]):
        if current == previous + timedelta(days=1):
            run += 1
            longest = max(longest, run)
        else:
            run = 1

    current_streak = 0
    cursor = reference_date
    if cursor not in active_dates:
        # The current UTC day is still in progress.  Do not mark an ongoing
        # streak as broken until a full inactive day has passed.
        cursor -= timedelta(days=1)
    while cursor in active_dates:
        current_streak += 1
        cursor -= timedelta(days=1)
    return current_streak, longest


def _month_keys(reference_date: date, count: int) -> list[str]:
    """Return ``count`` YYYY-MM keys ending at the reference month."""

    month_index = reference_date.year * 12 + reference_date.month - 1
    keys: list[str] = []
    for offset in range(count - 1, -1, -1):
        absolute = month_index - offset
        year, zero_based_month = divmod(absolute, 12)
        keys.append(f"{year:04d}-{zero_based_month + 1:02d}")
    return keys


def build_aggregate(
    repositories: Sequence[Repository],
    *,
    generated_at: datetime,
    days: int,
    author: str | None = None,
) -> dict[str, Any]:
    """Aggregate repository snapshots into a JSON-serialisable dictionary.

    ``repositories`` normally comes from
    :func:`gitstats_card.collect.collect_repositories`.  Their commits may be a
    slight superset of the window; this function applies the exact inclusive
    window and optional author filter.
    """

    if days <= 0:
        raise ValueError("days must be a positive integer")
    if not author:
        author = None

    reference = _as_utc(generated_at).replace(microsecond=0)
    window_start = reference - timedelta(days=days)
    start_epoch = int(window_start.timestamp())
    end_epoch = int(reference.timestamp())
    reference_date = reference.date()

    matched: list[tuple[Repository, Commit]] = []
    repository_commit_counts: Counter[str] = Counter()
    for repository in repositories:
        for commit in repository.commits:
            if not (start_epoch <= commit.timestamp <= end_epoch):
                continue
            if not _author_matches(commit, author):
                continue
            matched.append((repository, commit))
            repository_commit_counts[str(repository.path)] += 1

    commit_dates = [
        datetime.fromtimestamp(commit.timestamp, tz=timezone.utc).date()
        for _repository, commit in matched
    ]
    active_dates = set(commit_dates)
    current_streak, longest_streak = _streaks(active_dates, reference_date)

    hour_counts = [0] * 24
    for _repository, commit in matched:
        hour = datetime.fromtimestamp(commit.timestamp, tz=timezone.utc).hour
        hour_counts[hour] += 1

    month_counts = Counter(f"{day.year:04d}-{day.month:02d}" for day in commit_dates)
    months = [
        {"month": key, "commits": month_counts.get(key, 0)}
        for key in _month_keys(reference_date, STRIP_MONTHS)
    ]

    language_counts: Counter[str] = Counter()
    for repository in repositories:
        for filename in repository.files:
            language_counts[language_for_filename(filename)] += 1
    languages = [
        {"name": name, "files": files}
        for name, files in sorted(
            language_counts.items(), key=lambda item: (-item[1], item[0].casefold())
        )
    ]

    repositories_data: list[dict[str, Any]] = []
    for repository in repositories:
        repositories_data.append(
            {
                "name": repository.name,
                "path": str(repository.path),
                "commits": repository_commit_counts[str(repository.path)],
            }
        )
    repositories_data.sort(
        key=lambda item: (
            -int(item["commits"]),
            str(item["name"]).casefold(),
            str(item["path"]),
        )
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _format_instant(reference),
        "author": author,
        "window": {
            "days": days,
            "since": _format_instant(window_start),
            "until": _format_instant(reference),
            "timezone": "UTC",
        },
        "totals": {
            "commits": len(matched),
            "active_days": len(active_dates),
            "current_streak": current_streak,
            "longest_streak": longest_streak,
            "repositories": len(repositories),
        },
        "months": months,
        "commits_by_hour": hour_counts,
        "languages": languages,
        "repositories": repositories_data,
        "top_repositories": repositories_data[:TOP_REPOSITORIES],
    }
