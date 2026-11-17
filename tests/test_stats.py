"""Unit tests for pure aggregation, calendar streaks, and language buckets."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from gitstats_card.collect import Commit, Repository, collect_repositories
from gitstats_card.stats import build_aggregate, language_for_filename
from tests.support import (
    ALICE_EMAIL,
    ALICE_NAME,
    BROAD_SINCE,
    REFERENCE,
    build_standard_repositories,
)


class StatsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(tempfile.mkdtemp(prefix="gitstats-card-stats-"))
        cls.addClassCleanup(shutil.rmtree, cls.root, True)
        build_standard_repositories(cls.root)
        collected = collect_repositories(
            [cls.root], since=BROAD_SINCE, until=REFERENCE
        )
        cls.repositories = collected.repositories
        assert not collected.warnings, collected.warnings

    def aggregate(self, **kwargs):
        options = {"generated_at": REFERENCE, "days": 3650}
        options.update(kwargs)
        return build_aggregate(self.repositories, **options)

    def test_totals_active_days_and_streaks(self) -> None:
        stats = self.aggregate()
        totals = stats["totals"]
        self.assertEqual(totals["commits"], 8)
        self.assertEqual(totals["active_days"], 6)
        self.assertEqual(totals["current_streak"], 5)
        self.assertEqual(totals["longest_streak"], 5)
        self.assertEqual(totals["repositories"], 2)
        self.assertEqual(stats["schema_version"], 1)
        self.assertEqual(stats["window"]["timezone"], "UTC")
        self.assertEqual(stats["window"]["days"], 3650)

    def test_current_streak_has_a_one_day_grace_period(self) -> None:
        next_day = self.aggregate(
            generated_at=datetime(2024, 3, 15, 23, 0, tzinfo=timezone.utc)
        )
        self.assertEqual(next_day["totals"]["current_streak"], 5)
        self.assertEqual(next_day["totals"]["longest_streak"], 5)
        self.assertEqual(next_day["totals"]["active_days"], 6)

        after_a_full_inactive_day = self.aggregate(
            generated_at=datetime(2024, 3, 17, 23, 0, tzinfo=timezone.utc)
        )
        self.assertEqual(after_a_full_inactive_day["totals"]["current_streak"], 0)
        self.assertEqual(after_a_full_inactive_day["totals"]["longest_streak"], 5)

    def test_rolling_window_excludes_older_commits(self) -> None:
        stats = self.aggregate(days=3)
        self.assertEqual(stats["totals"]["commits"], 5)
        self.assertEqual(stats["totals"]["active_days"], 3)
        self.assertEqual(stats["totals"]["current_streak"], 3)
        self.assertEqual(stats["totals"]["longest_streak"], 3)
        march = next(item for item in stats["months"] if item["month"] == "2024-03")
        self.assertEqual(march["commits"], 5)

    def test_month_buckets_cover_last_twelve_months(self) -> None:
        stats = self.aggregate()
        labels = [item["month"] for item in stats["months"]]
        self.assertEqual(len(labels), 12)
        self.assertEqual(labels[0], "2023-04")
        self.assertEqual(labels[-1], "2024-03")
        by_month = {item["month"]: item["commits"] for item in stats["months"]}
        self.assertEqual(by_month["2024-02"], 1)
        self.assertEqual(by_month["2024-03"], 7)
        self.assertEqual(sum(by_month.values()), 8)

    def test_hour_histogram_is_utc_and_counts_every_commit(self) -> None:
        stats = self.aggregate()
        hours = stats["commits_by_hour"]
        self.assertEqual(len(hours), 24)
        self.assertEqual(sum(hours), 8)
        self.assertEqual(hours[8], 1)
        self.assertEqual(hours[9], 2)
        self.assertEqual(hours[10], 2)
        self.assertEqual(hours[11], 1)
        self.assertEqual(hours[12], 1)
        self.assertEqual(hours[18], 1)
        self.assertEqual(hours[0], 0)

    def test_language_mix_by_tracked_file_extension(self) -> None:
        stats = self.aggregate()
        self.assertEqual(
            stats["languages"],
            [
                {"name": "Python", "files": 3},
                {"name": "Markdown", "files": 2},
                {"name": "JavaScript", "files": 1},
                {"name": "Text", "files": 1},
                {"name": "Unknown", "files": 1},
            ],
        )
        self.assertEqual(language_for_filename("src/app.PY"), "Python")
        self.assertEqual(language_for_filename("Makefile"), "Unknown")
        self.assertEqual(language_for_filename(".gitignore"), "Unknown")

    def test_author_filter_is_case_insensitive_and_applies_to_all_metrics(self) -> None:
        stats = self.aggregate(author="ALICE")
        self.assertEqual(stats["author"], "ALICE")
        self.assertEqual(stats["totals"]["commits"], 6)
        self.assertEqual(stats["totals"]["active_days"], 5)
        self.assertEqual(stats["totals"]["current_streak"], 2)
        self.assertEqual(stats["totals"]["longest_streak"], 2)

        repositories = {item["name"]: item["commits"] for item in stats["repositories"]}
        self.assertEqual(repositories, {"alpha": 5, "beta": 1})
        self.assertEqual(stats["top_repositories"][0]["name"], "alpha")
        by_month = {item["month"]: item["commits"] for item in stats["months"]}
        self.assertEqual(by_month["2024-02"], 1)
        self.assertEqual(by_month["2024-03"], 5)

        # Language evidence is repository-wide, not commit- or author-scoped.
        self.assertEqual(sum(item["files"] for item in stats["languages"]), 8)
        email_stats = self.aggregate(author=ALICE_EMAIL.upper())
        self.assertEqual(email_stats["totals"]["commits"], 6)
        # The filter itself is preserved verbatim in the raw output.
        self.assertEqual(email_stats["author"], ALICE_EMAIL.upper())

    def test_per_repository_counts_and_top_five(self) -> None:
        stats = self.aggregate()
        repositories = {item["name"]: item["commits"] for item in stats["repositories"]}
        self.assertEqual(repositories, {"alpha": 6, "beta": 2})
        self.assertEqual(len(stats["top_repositories"]), 2)
        self.assertEqual(stats["top_repositories"], stats["repositories"])

        synthetic = []
        for index in range(7):
            repository_path = self.root / f"synthetic-{index}"
            synthetic.append(
                Repository(
                    path=repository_path,
                    commits=(
                        Commit(
                            timestamp=int(REFERENCE.timestamp()) - index,
                            author_name=ALICE_NAME,
                            author_email=ALICE_EMAIL,
                        ),
                    ),
                    files=(),
                )
            )
        many = build_aggregate(
            synthetic, generated_at=REFERENCE, days=365, author=None
        )
        self.assertEqual(len(many["repositories"]), 7)
        self.assertEqual(len(many["top_repositories"]), 5)

    def test_window_boundaries_are_inclusive(self) -> None:
        reference = datetime(2024, 3, 14, 12, 0, tzinfo=timezone.utc)
        end = int(reference.timestamp())
        start = end - 24 * 60 * 60
        commits = tuple(
            Commit(timestamp=timestamp, author_name=ALICE_NAME, author_email=ALICE_EMAIL)
            for timestamp in (start - 1, start, end, end + 1)
        )
        repository = Repository(
            path=self.root / "boundary",
            commits=commits,
            files=(),
        )
        stats = build_aggregate(
            [repository], generated_at=reference, days=1, author=None
        )
        self.assertEqual(stats["totals"]["commits"], 2)
        self.assertEqual(stats["totals"]["active_days"], 2)
        self.assertEqual(stats["totals"]["current_streak"], 2)
        self.assertEqual(stats["totals"]["longest_streak"], 2)

    def test_empty_repository_produces_zeroed_aggregate(self) -> None:
        empty = Repository(path=self.root / "empty", commits=(), files=())
        stats = build_aggregate([empty], generated_at=REFERENCE, days=365)
        self.assertEqual(
            stats["totals"],
            {
                "commits": 0,
                "active_days": 0,
                "current_streak": 0,
                "longest_streak": 0,
                "repositories": 1,
            },
        )
        self.assertEqual(len(stats["months"]), 12)
        self.assertEqual(sum(item["commits"] for item in stats["months"]), 0)
        self.assertEqual(stats["languages"], [])
        self.assertEqual(stats["commits_by_hour"], [0] * 24)
        self.assertEqual(stats["repositories"][0]["commits"], 0)
        self.assertEqual(stats["top_repositories"][0]["commits"], 0)

    def test_invalid_window_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.aggregate(days=0)


if __name__ == "__main__":
    unittest.main()
