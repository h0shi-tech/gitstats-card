"""Tests for repository discovery, safe git parsing, and edge cases."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from gitstats_card.collect import (
    InputPathError,
    RepositoryError,
    _parse_tracked_files,
    collect_repositories,
    discover_repositories,
    read_repository,
)
from tests.support import (
    BROAD_SINCE,
    REFERENCE,
    GitRepo,
    build_standard_repositories,
    temporary_directory,
)


class CollectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = temporary_directory(self)

    def test_discovers_repositories_recursively_and_deduplicates(self) -> None:
        workspace = self.tmp / "workspace"
        build_standard_repositories(workspace)
        discovered = discover_repositories([workspace, workspace / "alpha"])
        self.assertEqual([path.name for path in discovered], ["alpha", "beta"])
        self.assertEqual(len(discovered), len(set(discovered)))

    def test_exclude_glob_matches_name_and_relative_path(self) -> None:
        workspace = self.tmp / "workspace"
        build_standard_repositories(workspace)
        self.assertEqual(
            [path.name for path in discover_repositories([workspace], exclude=["beta"])],
            ["alpha"],
        )
        self.assertEqual(
            [
                path.name
                for path in discover_repositories(
                    [self.tmp], exclude=["workspace/beta"]
                )
            ],
            ["alpha"],
        )
        self.assertEqual(
            discover_repositories([workspace], exclude=["*"]),
            (),
        )

    def test_collects_commits_and_tracked_files_with_nul_parsing(self) -> None:
        workspace = self.tmp / "workspace"
        build_standard_repositories(workspace)
        result = collect_repositories(
            [workspace], since=BROAD_SINCE, until=REFERENCE
        )
        self.assertEqual(result.discovered, 2)
        self.assertEqual(result.warnings, ())
        by_name = {repository.name: repository for repository in result.repositories}
        self.assertEqual(len(by_name["alpha"].commits), 6)
        self.assertEqual(len(by_name["beta"].commits), 2)
        self.assertIn("notes with spaces.md", by_name["alpha"].files)
        self.assertEqual(by_name["beta"].files, ("notes.txt", "script.py"))

    def test_git_log_window_limits_collection(self) -> None:
        workspace = self.tmp / "workspace"
        build_standard_repositories(workspace)
        result = collect_repositories(
            [workspace],
            since=datetime(2024, 3, 13, 0, 0, tzinfo=timezone.utc),
            until=REFERENCE,
        )
        by_name = {repository.name: repository for repository in result.repositories}
        self.assertEqual(len(by_name["alpha"].commits), 2)
        self.assertEqual(len(by_name["beta"].commits), 1)

    def test_empty_repo_and_detached_head_are_supported(self) -> None:
        empty = GitRepo.create(self.tmp, "empty")
        detached = GitRepo.create(self.tmp, "detached")
        detached.write("main.py", "print('detached')\n")
        detached.commit(datetime(2024, 3, 14, 9, 0, tzinfo=timezone.utc), "detached")
        detached.detach_head()

        result = collect_repositories(
            [self.tmp], since=BROAD_SINCE, until=REFERENCE
        )
        self.assertEqual(result.discovered, 2)
        self.assertEqual(result.warnings, ())
        by_name = {repository.name: repository for repository in result.repositories}
        self.assertEqual(len(by_name["empty"].commits), 0)
        self.assertEqual(by_name["empty"].files, ())
        self.assertEqual(len(by_name["detached"].commits), 1)
        self.assertEqual(by_name["detached"].files, ("main.py",))

        empty_snapshot = read_repository(
            empty.path, since=BROAD_SINCE, until=REFERENCE
        )
        self.assertEqual(empty_snapshot.commits, ())
        self.assertEqual(empty_snapshot.files, ())

    def test_unreadable_git_marker_is_a_warning_not_a_crash(self) -> None:
        valid = GitRepo.create(self.tmp, "valid")
        valid.write("ok.py", "VALUE = 1\n")
        valid.commit(datetime(2024, 3, 14, 9, 0, tzinfo=timezone.utc), "valid")

        broken = self.tmp / "broken"
        (broken / ".git").mkdir(parents=True)

        result = collect_repositories(
            [self.tmp], since=BROAD_SINCE, until=REFERENCE
        )
        self.assertEqual(result.discovered, 2)
        self.assertEqual([repository.name for repository in result.repositories], ["valid"])
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("skipping", result.warnings[0])
        self.assertIn("not a readable git repository", result.warnings[0])

    def test_no_repository_and_bad_input_paths(self) -> None:
        plain = self.tmp / "plain"
        plain.mkdir()
        result = collect_repositories(
            [plain], since=BROAD_SINCE, until=REFERENCE
        )
        self.assertEqual(result.discovered, 0)
        self.assertEqual(result.repositories, ())
        self.assertEqual(result.warnings, ())

        with self.assertRaises(InputPathError):
            discover_repositories([self.tmp / "missing"])
        with self.assertRaises(InputPathError):
            discover_repositories([plain / "file.txt"])

        file_path = plain / "file.txt"
        file_path.write_text("not a directory", encoding="utf-8")
        with self.assertRaises(InputPathError):
            discover_repositories([file_path])
        with self.assertRaises(RepositoryError):
            read_repository(plain, since=BROAD_SINCE, until=REFERENCE)

    def test_filenames_with_spaces_and_unicode_survive_collection(self) -> None:
        repo = GitRepo.create(self.tmp, "names")
        repo.write("a file with spaces.py", "print('spaces')\n")
        repo.write("café.md", "unicode\n")
        repo.commit(datetime(2024, 3, 14, 9, 0, tzinfo=timezone.utc), "names")
        snapshot = read_repository(repo.path, since=BROAD_SINCE, until=REFERENCE)
        self.assertEqual(
            snapshot.files,
            ("a file with spaces.py", "café.md"),
        )

        # APFS cannot store invalid UTF-8 names, but the parser intentionally
        # preserves surrogate-escaped bytes on platforms that can.
        self.assertEqual(
            _parse_tracked_files("ok.py\0bad\udcff.py\0"),
            ("ok.py", "bad\udcff.py"),
        )


if __name__ == "__main__":
    unittest.main()
