"""End-to-end CLI tests exercising exit codes and stdout contracts."""

from __future__ import annotations

import io
import json
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from gitstats_card.cli import MARKDOWN_SNIPPET, main
from tests.support import GitRepo, build_standard_repositories


class CliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(tempfile.mkdtemp(prefix="gitstats-card-cli-"))
        cls.addClassCleanup(shutil.rmtree, cls.root, True)
        cls.workspace = cls.root / "workspace"
        build_standard_repositories(cls.workspace)

    def invoke(self, arguments: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(arguments)
        return code, stdout.getvalue(), stderr.getvalue()

    def common_arguments(self, output: Path) -> list[str]:
        return [
            "--path",
            str(self.workspace),
            "--days",
            "3650",
            "--generated-at",
            "2024-03-14T23:00:00Z",
            "--out",
            str(output),
        ]

    def test_default_writes_svg_with_empty_stdout(self) -> None:
        output = self.root / "nested" / "stats.svg"
        code, stdout, stderr = self.invoke(self.common_arguments(output))
        self.assertEqual(code, 0)
        self.assertEqual(stdout, "")
        self.assertIn("wrote", stderr)
        self.assertTrue(output.exists())
        root = ET.fromstring(output.read_bytes())
        self.assertEqual(root.attrib["width"], "480")
        self.assertEqual(root.attrib["height"], "220")

    def test_json_prints_the_raw_aggregate(self) -> None:
        output = self.root / "json.svg"
        code, stdout, stderr = self.invoke(
            [*self.common_arguments(output), "--json"]
        )
        self.assertEqual(code, 0)
        self.assertTrue(output.exists())
        aggregate = json.loads(stdout)
        self.assertEqual(aggregate["totals"]["commits"], 8)
        self.assertEqual(aggregate["totals"]["active_days"], 6)
        self.assertEqual(aggregate["totals"]["current_streak"], 5)
        self.assertEqual(aggregate["totals"]["longest_streak"], 5)
        self.assertEqual(aggregate["window"]["until"], "2024-03-14T23:00:00Z")
        self.assertEqual(aggregate["author"], None)
        self.assertEqual(len(aggregate["months"]), 12)
        self.assertEqual(len(aggregate["commits_by_hour"]), 24)
        self.assertEqual(
            [repository["name"] for repository in aggregate["top_repositories"]],
            ["alpha", "beta"],
        )
        self.assertIn("wrote", stderr)

    def test_markdown_snippet_is_exact_and_stdout_only(self) -> None:
        output = self.root / "snippet.svg"
        code, stdout, stderr = self.invoke(
            [*self.common_arguments(output), "--markdown-snippet"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(stdout, MARKDOWN_SNIPPET + "\n")
        self.assertEqual(stdout, "![stats](./assets/stats.svg)\n")
        self.assertIn("wrote", stderr)
        self.assertTrue(output.exists())

    def test_output_is_byte_deterministic_across_invocations(self) -> None:
        first = self.root / "first.svg"
        second = self.root / "second.svg"
        code_one, _stdout_one, _stderr_one = self.invoke(self.common_arguments(first))
        code_two, _stdout_two, _stderr_two = self.invoke(
            self.common_arguments(second)
        )
        self.assertEqual(code_one, 0)
        self.assertEqual(code_two, 0)
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_author_filter_reaches_cli_output(self) -> None:
        output = self.root / "author.svg"
        code, stdout, _stderr = self.invoke(
            [*self.common_arguments(output), "--author", "alice", "--json"]
        )
        self.assertEqual(code, 0)
        aggregate = json.loads(stdout)
        self.assertEqual(aggregate["totals"]["commits"], 6)
        self.assertEqual(aggregate["author"], "alice")

    def test_no_repositories_returns_exit_code_2(self) -> None:
        empty = self.root / "empty-workspace"
        empty.mkdir(exist_ok=True)
        output = self.root / "missing.svg"
        code, stdout, stderr = self.invoke(
            ["--path", str(empty), "--out", str(output)]
        )
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("no git repositories found", stderr)
        self.assertFalse(output.exists())

    def test_empty_repository_is_a_successful_zero_card(self) -> None:
        empty = GitRepo.create(self.root, "empty-repo")
        output = self.root / "empty.svg"
        code, stdout, stderr = self.invoke(
            [
                "--path",
                str(empty.path),
                "--generated-at",
                "2024-03-14T23:00:00Z",
                "--out",
                str(output),
            ]
        )
        self.assertEqual(code, 0)
        self.assertEqual(stdout, "")
        self.assertTrue(output.exists())
        root = ET.fromstring(output.read_bytes())
        texts = [
            element.text
            for element in root.iter("{http://www.w3.org/2000/svg}text")
        ]
        self.assertIn("0", texts)
        self.assertIn("commits in 365 days", stderr)

    def test_missing_git_returns_exit_code_2(self) -> None:
        output = self.root / "missing-git.svg"
        with mock.patch("gitstats_card.collect.shutil.which", return_value=None):
            code, stdout, stderr = self.invoke(self.common_arguments(output))
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("git executable not found", stderr)
        self.assertFalse(output.exists())

    def test_bad_usage_returns_exit_code_2(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as raised:
                main(["--days", "0"])
        self.assertEqual(raised.exception.code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("days must be greater than zero", stderr.getvalue())

    def test_invalid_path_returns_exit_code_2(self) -> None:
        output = self.root / "invalid.svg"
        code, stdout, stderr = self.invoke(
            ["--path", str(self.root / "does-not-exist"), "--out", str(output)]
        )
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("path does not exist", stderr)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
