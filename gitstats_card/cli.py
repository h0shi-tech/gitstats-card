"""Command-line interface for gitstats-card."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

from . import __version__
from .collect import (
    GitStatsError,
    GitUnavailableError,
    InputPathError,
    collect_repositories,
    ensure_git_available,
)
from .stats import build_aggregate
from .svg import render_card

MARKDOWN_SNIPPET = "![stats](./assets/stats.svg)"
DEFAULT_OUTPUT = "assets/stats.svg"


def _positive_days(value: str) -> int:
    try:
        days = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("days must be an integer") from exc
    if days <= 0:
        raise argparse.ArgumentTypeError("days must be greater than zero")
    return days


def _generated_at(value: str) -> datetime:
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "expected an ISO 8601 date or timestamp, e.g. 2026-01-31T12:00:00Z"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gitstats-card",
        description=(
            "Build a self-hosted SVG coding-stats card from local git "
            "repositories. No GitHub token or image hosting service is used."
        ),
        epilog=(
            "Examples:\n"
            "  gitstats-card --path ~/code --out assets/stats.svg\n"
            "  gitstats-card --path . --days 90 --theme light --out card.svg\n"
            "  gitstats-card --path . --json\n"
            "  gitstats-card --path . --markdown-snippet\n\n"
            "The SVG is always written to --out. --json and --markdown-snippet "
            "additionally print their output to stdout."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "--path",
        dest="paths",
        metavar="DIR",
        action="append",
        help=(
            "repository or directory to scan recursively; repeat for multiple "
            "roots (default: current directory)"
        ),
    )
    parser.add_argument(
        "--out",
        default=DEFAULT_OUTPUT,
        metavar="FILE",
        help=f"path for the generated SVG (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--theme",
        choices=("dark", "light"),
        default="dark",
        help="card colour theme (default: dark)",
    )
    parser.add_argument(
        "--title",
        default="Coding Stats",
        help="card title (default: Coding Stats)",
    )
    parser.add_argument(
        "--days",
        type=_positive_days,
        default=365,
        metavar="N",
        help="rolling UTC window in days (default: 365)",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help=(
            "exclude repositories whose name, relative path, or absolute path "
            "matches GLOB; repeat for multiple patterns"
        ),
    )
    parser.add_argument(
        "--author",
        default=None,
        metavar="TEXT",
        help=(
            "only count commits whose author name or email contains TEXT "
            "(case-insensitive)"
        ),
    )
    parser.add_argument(
        "--generated-at",
        type=_generated_at,
        default=None,
        metavar="ISO8601",
        help=(
            "use this UTC date/timestamp as the window and streak reference, "
            "and print it on the card"
        ),
    )
    report_group = parser.add_mutually_exclusive_group()
    report_group.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="print the raw aggregate dictionary to stdout",
    )
    report_group.add_argument(
        "--markdown-snippet",
        action="store_true",
        help=(
            "print the markdown snippet '![stats](./assets/stats.svg)' to stdout"
        ),
    )
    return parser


def _write_card(path: Path, svg: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(svg)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process exit code (0 success, 2 usage/no repos)."""

    parser = build_parser()
    args = parser.parse_args(argv)

    reference = args.generated_at or datetime.now(timezone.utc).replace(microsecond=0)
    try:
        window_start = reference - timedelta(days=args.days)
    except OverflowError:
        parser.error("--days is too large for a representable calendar window")

    paths = args.paths or [str(Path.cwd())]
    try:
        ensure_git_available()
        result = collect_repositories(
            paths,
            since=window_start,
            until=reference,
            exclude=args.exclude,
        )
    except GitUnavailableError as exc:
        print(f"gitstats-card: error: {exc}", file=sys.stderr)
        return 2
    except InputPathError as exc:
        print(f"gitstats-card: error: {exc}", file=sys.stderr)
        return 2
    except GitStatsError as exc:
        print(f"gitstats-card: error: {exc}", file=sys.stderr)
        return 2

    for warning in result.warnings:
        print(f"gitstats-card: warning: {warning}", file=sys.stderr)

    if not result.repositories:
        roots = ", ".join(paths)
        if result.discovered:
            print(
                "gitstats-card: error: none of the discovered git "
                f"repositories could be read under: {roots}",
                file=sys.stderr,
            )
        else:
            print(
                f"gitstats-card: error: no git repositories found under: {roots}",
                file=sys.stderr,
            )
        return 2

    aggregate = build_aggregate(
        result.repositories,
        generated_at=reference,
        days=args.days,
        author=args.author,
    )

    svg = render_card(
        aggregate,
        theme=args.theme,
        title=args.title,
        generated_at=_iso_utc(reference) if args.generated_at is not None else None,
    )

    output_path = Path(args.out).expanduser()
    try:
        _write_card(output_path, svg)
    except OSError as exc:
        print(
            f"gitstats-card: error: cannot write {output_path}: {exc}",
            file=sys.stderr,
        )
        return 1

    totals = aggregate["totals"]
    print(
        "gitstats-card: wrote "
        f"{output_path} ({totals['commits']} commits in {args.days} days, "
        f"{len(result.repositories)} repositories)",
        file=sys.stderr,
    )

    if args.json_output:
        print(json.dumps(aggregate, indent=2, sort_keys=True, ensure_ascii=True))
    elif args.markdown_snippet:
        print(MARKDOWN_SNIPPET)
    return 0
