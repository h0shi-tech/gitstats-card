"""Deterministic SVG rendering for the gitstats card.

The output uses only inline SVG primitives.  There are no scripts, web fonts,
images, links, or remote resources, so GitHub's Camo image proxy can serve it
without fetching anything from a third party.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from xml.sax.saxutils import escape

CARD_WIDTH = 480
CARD_HEIGHT = 220

MONTH_ABBREVIATIONS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)

FONT_STACK = (
    "-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans',"
    "Helvetica,Arial,sans-serif"
)

# Characters that cannot legally appear in XML 1.0.  Surrogates can arrive via
# surrogateescape when a repository or tracked filename is not valid UTF-8.
_INVALID_XML_CHARS = re.compile(
    "[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]"
)


@dataclass(frozen=True, slots=True)
class Theme:
    background: str
    border: str
    text: str
    muted: str
    grid: str
    accent: str
    accent_alt: str
    language_colors: tuple[str, str, str]


THEMES: dict[str, Theme] = {
    "dark": Theme(
        background="#0d1117",
        border="#30363d",
        text="#c9d1d9",
        muted="#8b949e",
        grid="#21262d",
        accent="#58a6ff",
        accent_alt="#bc8cff",
        language_colors=("#58a6ff", "#3fb950", "#d29922"),
    ),
    "light": Theme(
        background="#ffffff",
        border="#d0d7de",
        text="#1f2328",
        muted="#656d76",
        grid="#eaeef2",
        accent="#0969da",
        accent_alt="#8250df",
        language_colors=("#0969da", "#1a7f37", "#9a6700"),
    ),
}


def _clean(value: object) -> str:
    """Make a scalar safe for a single line of XML text."""

    text = _INVALID_XML_CHARS.sub("\uFFFD", str(value))
    return " ".join(text.split())


def _escaped(value: object, limit: int | None = None) -> str:
    """Sanitize, optionally truncate, and XML-escape text or an attribute."""

    text = _clean(value)
    if limit is not None and len(text) > limit:
        text = text[: max(1, limit - 1)].rstrip() + "\u2026"
    return escape(text, {'"': "&quot;", "'": "&apos;"})


def _count_text(value: object) -> str:
    return f"{int(value):,}"


def _month_label(month_key: object) -> str:
    text = str(month_key)
    try:
        month = int(text.rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return text[-3:]
    if 1 <= month <= 12:
        return MONTH_ABBREVIATIONS[month - 1]
    return text[-3:]


def _bar_height(count: int, maximum: int, available: int) -> int:
    if count <= 0 or maximum <= 0:
        return 0
    return max(2, round(available * count / maximum))


def _language_display(
    languages: Sequence[Mapping[str, Any]], theme: Theme
) -> list[tuple[str, int, str]]:
    """Return up to four legend entries, folding the tail into ``Other``."""

    entries = [
        (str(item["name"]), int(item["files"]))
        for item in languages
        if int(item["files"])
    ]
    colors = [*theme.language_colors, theme.muted]
    if len(entries) <= 4:
        return [
            (name, files, colors[index]) for index, (name, files) in enumerate(entries)
        ]

    head = [
        (name, files, colors[index]) for index, (name, files) in enumerate(entries[:3])
    ]
    other_count = sum(files for _name, files in entries[3:])
    head.append(("Other", other_count, colors[3]))
    return head


def _language_bar(
    entries: Sequence[tuple[str, int, str]], total_files: int
) -> list[str]:
    if not entries or total_files <= 0:
        return []

    bar_x = 22
    bar_width = 436
    consumed = 0
    cumulative = 0
    lines: list[str] = []
    for _name, files, color in entries:
        cumulative += files
        end = round(bar_width * cumulative / total_files)
        width = end - consumed
        if width > 0:
            lines.append(
                f'        <rect x="{bar_x + consumed}" y="135" '
                f'width="{width}" height="6" fill="{color}"/>'
            )
        consumed = end
    return lines


def render_card(
    stats: Mapping[str, Any],
    *,
    theme: str = "dark",
    title: str = "Coding Stats",
    generated_at: str | None = None,
) -> str:
    """Render an aggregate dictionary as a complete, deterministic SVG string.

    The same aggregate, theme, title, and generated-at value always produce
    identical bytes.  No clock value is read here, and the generated timestamp
    is omitted entirely unless ``generated_at`` is supplied.
    """

    try:
        tokens = THEMES[theme]
    except KeyError as exc:
        raise ValueError(f"unknown theme {theme!r}; expected one of {sorted(THEMES)}") from exc

    totals = stats["totals"]
    total_commits = int(totals["commits"])
    active_days = int(totals["active_days"])
    current_streak = int(totals["current_streak"])
    longest_streak = int(totals["longest_streak"])
    repository_count = int(totals["repositories"])
    days = int(stats["window"]["days"])
    months: Sequence[Mapping[str, Any]] = stats["months"]
    hour_counts = [int(value) for value in stats["commits_by_hour"]]
    languages: Sequence[Mapping[str, Any]] = stats["languages"]
    top_repositories: Sequence[Mapping[str, Any]] = stats["top_repositories"]

    total_files = sum(int(item["files"]) for item in languages)
    language_entries = _language_display(languages, tokens)

    repository_word = "repository" if repository_count == 1 else "repositories"
    subtitle = f"{days}-day window \u00b7 {repository_count} {repository_word}"
    if top_repositories and int(top_repositories[0]["commits"]) > 0:
        top_name = _escaped(top_repositories[0]["name"], limit=28)
        subtitle += f" \u00b7 top {top_name}"
    # The subtitle contains already-escaped repo text, so only its static
    # prefix is appended below and the assembled value is not escaped twice.

    if generated_at:
        generated_label = f"generated {str(generated_at)[:10]}"
    else:
        generated_label = "self-hosted"

    top_language_text = ""
    if language_entries:
        top_language_text = f" Top language: {language_entries[0][0]}."
    card_description = (
        f"{_clean(title)}: {total_commits} commits across {repository_count} "
        f"{repository_word}; {active_days} active days, a {current_streak}-day "
        f"current streak, and a {longest_streak}-day longest streak."
        f"{top_language_text}"
    )

    max_month = max((int(item["commits"]) for item in months), default=0)
    max_hour = max(hour_counts, default=0)
    peak_hour = hour_counts.index(max_hour) if max_hour > 0 else None

    lines: list[str] = []
    add = lines.append
    add('<?xml version="1.0" encoding="UTF-8"?>')
    add(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{CARD_WIDTH}" '
        f'height="{CARD_HEIGHT}" viewBox="0 0 {CARD_WIDTH} {CARD_HEIGHT}" '
        f'role="img" aria-labelledby="card-title card-desc">'
    )
    add(f'  <title id="card-title">{_escaped(title)} \u2014 coding statistics</title>')
    add(f'  <desc id="card-desc">{_escaped(card_description)}</desc>')
    add("  <defs>")
    add(
        '    <linearGradient id="accent" x1="0%" y1="0%" x2="100%" y2="0%">'
    )
    add(f'      <stop offset="0%" stop-color="{tokens.accent}"/>')
    add(f'      <stop offset="100%" stop-color="{tokens.accent_alt}"/>')
    add("    </linearGradient>")
    add(
        '    <clipPath id="language-clip">'
        '<rect x="22" y="135" width="436" height="6" rx="3"/>'
        "</clipPath>"
    )
    add("  </defs>")
    add(f'  <g font-family="{FONT_STACK}">')
    add(
        f'    <rect x="1" y="1" width="478" height="218" rx="14" '
        f'fill="{tokens.background}" stroke="{tokens.border}" stroke-width="1"/>'
    )
    add(
        f'    <rect x="22" y="1" width="436" height="2" rx="1" '
        f'fill="url(#accent)" opacity="0.9"/>'
    )

    # Header
    add(
        f'    <rect x="22" y="16" width="24" height="24" rx="7" '
        f'fill="url(#accent)" opacity="0.16"/>'
    )
    add(
        '    <path d="M27.5 32.5 L32 27.5 L36 30.5 L40.5 23.5" fill="none" '
        'stroke="url(#accent)" stroke-width="2" stroke-linecap="round" '
        'stroke-linejoin="round"/>'
    )
    add(
        f'    <circle cx="40.5" cy="23.5" r="1.7" fill="{tokens.accent_alt}"/>'
    )
    add(
        f'    <text x="56" y="29" font-size="15" font-weight="600" '
        f'fill="{tokens.text}">{_escaped(title, limit=36)}</text>'
    )
    add(
        f'    <text x="458" y="28" font-size="8.5" letter-spacing="0.4" '
        f'fill="{tokens.muted}" text-anchor="end">{_escaped(generated_label, limit=27)}</text>'
    )
    add(
        f'    <text x="56" y="45" font-size="9.5" fill="{tokens.muted}">'
        f"{subtitle}</text>"
    )
    add(
        f'    <line x1="22" y1="58" x2="458" y2="58" '
        f'stroke="{tokens.grid}" stroke-width="1"/>'
    )

    # Headline numbers
    add(
        f'    <text x="22" y="86" font-size="27" font-weight="700" '
        f'fill="{tokens.text}">{_count_text(total_commits)}</text>'
    )
    add(
        f'    <text x="140" y="86" font-size="21" font-weight="600" '
        f'fill="{tokens.text}">{_count_text(active_days)}</text>'
    )
    add(
        f'    <text x="256" y="86" font-size="21" font-weight="600" '
        f'fill="{tokens.accent}">{_count_text(current_streak)}</text>'
    )
    add(
        f'    <text x="372" y="86" font-size="21" font-weight="600" '
        f'fill="{tokens.text}">{_count_text(longest_streak)}</text>'
    )
    label_style = (
        f'font-size="8" font-weight="600" letter-spacing="1.1" '
        f'fill="{tokens.muted}"'
    )
    add(f'    <text x="22" y="102" {label_style}>COMMITS</text>')
    add(f'    <text x="140" y="102" {label_style}>ACTIVE DAYS</text>')
    add(f'    <text x="256" y="102" {label_style}>CURRENT STREAK</text>')
    add(f'    <text x="372" y="102" {label_style}>LONGEST STREAK</text>')
    for divider_x in (126, 242, 358):
        add(
            f'    <line x1="{divider_x}" y1="69" x2="{divider_x}" y2="101" '
            f'stroke="{tokens.grid}" stroke-width="1"/>'
        )

    add(
        f'    <line x1="22" y1="112" x2="458" y2="112" '
        f'stroke="{tokens.grid}" stroke-width="1"/>'
    )

    # Languages
    add(
        f'    <text x="22" y="128" font-size="8" font-weight="600" '
        f'letter-spacing="1.2" fill="{tokens.muted}">LANGUAGES</text>'
    )
    tracked_label = f"{total_files:,} tracked file" + (
        "" if total_files == 1 else "s"
    )
    add(
        f'    <text x="458" y="128" font-size="8.5" fill="{tokens.muted}" '
        f'text-anchor="end">{tracked_label}</text>'
    )
    add(
        f'    <rect x="22" y="135" width="436" height="6" rx="3" '
        f'fill="{tokens.grid}"/>'
    )
    if language_entries:
        add('    <g clip-path="url(#language-clip)">')
        lines.extend(_language_bar(language_entries, total_files))
        add("    </g>")
        for index, (name, files, color) in enumerate(language_entries):
            x = 22 + index * 109
            percentage = round(files * 100 / total_files) if total_files else 0
            legend = _escaped(f"{name} {percentage}%", limit=20)
            add(f'    <circle cx="{x + 3}" cy="151.5" r="3" fill="{color}"/>')
            add(
                f'    <text x="{x + 10}" y="155" font-size="8.5" '
                f'fill="{tokens.muted}">{legend}</text>'
            )
    else:
        add(
            f'    <text x="22" y="155" font-size="8.5" '
            f'fill="{tokens.muted}">No tracked files</text>'
        )

    add(
        f'    <line x1="22" y1="163" x2="458" y2="163" '
        f'stroke="{tokens.grid}" stroke-width="1"/>'
    )

    # Charts
    add(
        f'    <text x="22" y="177" font-size="8" font-weight="600" '
        f'letter-spacing="1.1" fill="{tokens.muted}">MONTHLY COMMITS</text>'
    )
    if peak_hour is None:
        hours_heading = "ACTIVE HOURS \u00b7 UTC"
    else:
        hours_heading = f"ACTIVE HOURS \u00b7 PEAK {peak_hour:02d}:00 UTC"
    add(
        f'    <text x="458" y="177" font-size="8" font-weight="600" '
        f'letter-spacing="1.1" fill="{tokens.muted}" text-anchor="end">'
        f"{hours_heading}</text>"
    )
    add(
        f'    <line x1="22" y1="200" x2="286" y2="200" '
        f'stroke="{tokens.grid}" stroke-width="1"/>'
    )
    add(
        f'    <line x1="312" y1="200" x2="454" y2="200" '
        f'stroke="{tokens.grid}" stroke-width="1"/>'
    )

    for index, month in enumerate(months):
        count = int(month["commits"])
        height = _bar_height(count, max_month, 18)
        x = 22 + index * 22
        label = _month_label(month["month"])
        count_word = "commit" if count == 1 else "commits"
        if height:
            add(
                f'    <rect x="{x}" y="{200 - height}" width="13" '
                f'height="{height}" rx="2" fill="url(#accent)" opacity="0.9">'
                f"<title>{_escaped(month['month'])}: {count} {count_word}</title>"
                f"</rect>"
            )
        add(
            f'    <text x="{x + 6.5}" y="212" font-size="7" '
            f'fill="{tokens.muted}" text-anchor="middle">{_escaped(label)}</text>'
        )

    for hour, count in enumerate(hour_counts[:24]):
        height = _bar_height(count, max_hour, 18)
        x = 312 + hour * 6
        if height:
            fill = tokens.accent if hour == peak_hour else tokens.accent_alt
            opacity = "0.95" if hour == peak_hour else "0.55"
            count_word = "commit" if count == 1 else "commits"
            add(
                f'    <rect x="{x}" y="{200 - height}" width="4" '
                f'height="{height}" rx="1" fill="{fill}" opacity="{opacity}">'
                f"<title>{hour:02d}:00 UTC: {count} {count_word}</title></rect>"
            )
    add(
        f'    <text x="312" y="212" font-size="7" '
        f'fill="{tokens.muted}">00</text>'
    )
    add(
        f'    <text x="454" y="212" font-size="7" text-anchor="end" '
        f'fill="{tokens.muted}">23</text>'
    )

    add("  </g>")
    add("</svg>")
    return "\n".join(lines) + "\n"
