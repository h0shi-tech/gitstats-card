"""Tests for deterministic SVG rendering, escaping, and themes."""

from __future__ import annotations

import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from gitstats_card.collect import collect_repositories
from gitstats_card.stats import build_aggregate
from gitstats_card.svg import CARD_HEIGHT, CARD_WIDTH, render_card
from tests.support import (
    BROAD_SINCE,
    REFERENCE,
    GitRepo,
    build_standard_repositories,
    temporary_directory,
)

SVG_NS = "http://www.w3.org/2000/svg"


class SvgTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(tempfile.mkdtemp(prefix="gitstats-card-svg-"))
        cls.addClassCleanup(shutil.rmtree, cls.root, True)
        build_standard_repositories(cls.root)
        cls.stats = build_aggregate(
            collect_repositories(
                [cls.root], since=BROAD_SINCE, until=REFERENCE
            ).repositories,
            generated_at=REFERENCE,
            days=3650,
        )

    def test_card_is_valid_xml_with_expected_dimensions(self) -> None:
        svg = render_card(self.stats)
        root = ET.fromstring(svg.encode("utf-8"))
        self.assertEqual(root.tag, f"{{{SVG_NS}}}svg")
        self.assertEqual(root.attrib["width"], str(CARD_WIDTH))
        self.assertEqual(root.attrib["height"], str(CARD_HEIGHT))
        self.assertEqual(root.attrib["viewBox"], f"0 0 {CARD_WIDTH} {CARD_HEIGHT}")
        self.assertEqual(root.attrib["role"], "img")
        self.assertTrue(svg.startswith('<?xml version="1.0" encoding="UTF-8"?>'))

    def test_rendering_is_byte_for_byte_deterministic(self) -> None:
        first = render_card(
            self.stats,
            theme="dark",
            title="Vlad Nikishin",
            generated_at="2024-03-14T23:00:00Z",
        )
        second = render_card(
            self.stats,
            theme="dark",
            title="Vlad Nikishin",
            generated_at="2024-03-14T23:00:00Z",
        )
        self.assertEqual(first, second)
        self.assertEqual(first.encode("utf-8"), second.encode("utf-8"))

    def test_theme_tokens_are_applied(self) -> None:
        dark = render_card(self.stats, theme="dark")
        self.assertIn('fill="#0d1117"', dark)
        self.assertIn('fill="#c9d1d9"', dark)
        self.assertIn('stop-color="#58a6ff"', dark)
        self.assertIn('stop-color="#bc8cff"', dark)

        light = render_card(self.stats, theme="light")
        self.assertIn('fill="#ffffff"', light)
        self.assertIn('fill="#1f2328"', light)
        self.assertNotIn("#0d1117", light)
        self.assertIn('stop-color="#0969da"', light)
        with self.assertRaises(ValueError):
            render_card(self.stats, theme="sepia")

    def test_generated_timestamp_is_opt_in(self) -> None:
        without_timestamp = render_card(self.stats)
        self.assertNotIn("2024-03-14", without_timestamp)
        self.assertNotIn("generated ", without_timestamp)

        with_timestamp = render_card(
            self.stats, generated_at="2024-03-14T23:00:00Z"
        )
        self.assertIn("generated 2024-03-14", with_timestamp)

    def test_repo_name_and_title_are_xml_escaped(self) -> None:
        special = GitRepo.create(self.root, 'evil<&">')
        special.write("code.py", "print('safe')\n")
        special.commit(
            datetime(2024, 3, 14, 9, 0, tzinfo=timezone.utc),
            "special repository",
        )
        stats = build_aggregate(
            collect_repositories(
                [special.path], since=BROAD_SINCE, until=REFERENCE
            ).repositories,
            generated_at=REFERENCE,
            days=365,
        )
        title = 'R&D <script>alert("x")</script>'
        svg = render_card(stats, title=title)

        root = ET.fromstring(svg.encode("utf-8"))
        self.assertEqual(
            root.find(f"{{{SVG_NS}}}title").text,
            f'{title} \u2014 coding statistics',
        )
        self.assertIn("evil&lt;&amp;&quot;&gt;", svg)
        self.assertIn("R&amp;D &lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;", svg)
        self.assertNotIn("<script", svg.lower())
        self.assertNotIn('<evil<&">', svg)

    def test_only_inline_resources_and_no_scripts(self) -> None:
        svg = render_card(self.stats)
        lowered = svg.lower()
        self.assertNotIn("<script", lowered)
        self.assertNotIn("<image", lowered)
        self.assertNotIn("@import", lowered)
        self.assertEqual(lowered.count("http"), 1)
        self.assertIn('xmlns="http://www.w3.org/2000/svg"', svg)

        root = ET.fromstring(svg.encode("utf-8"))
        for element in root.iter():
            self.assertNotIn(element.tag.rsplit("}", 1)[-1], {"script", "image"})
            for attribute in element.attrib:
                self.assertNotIn(attribute.rsplit("}", 1)[-1], {"href"})
        self.assertIn('url(#accent)', svg)
        self.assertIn('url(#language-clip)', svg)

    def test_empty_aggregate_still_renders_valid_svg(self) -> None:
        empty = build_aggregate([], generated_at=REFERENCE, days=365)
        svg = render_card(empty, theme="light")
        root = ET.fromstring(svg.encode("utf-8"))
        self.assertEqual(root.attrib["width"], "480")
        self.assertNotIn("<script", svg.lower())
        self.assertIn("No tracked files", svg)
        self.assertIn("0 commits", svg)

    def test_escaping_works_for_a_repo_in_a_root_with_a_quote(self) -> None:
        # This is a regression guard for paths flowing through discovery rather
        # than being passed directly to read_repository.
        base = temporary_directory(self)
        repo = GitRepo.create(base, 'quoted"name')
        repo.write("main.py", "print('x')\n")
        repo.commit(
            datetime(2024, 3, 14, 9, 0, tzinfo=timezone.utc), "quoted path"
        )
        stats = build_aggregate(
            collect_repositories(
                [base], since=BROAD_SINCE, until=REFERENCE
            ).repositories,
            generated_at=REFERENCE,
            days=365,
        )
        svg = render_card(stats)
        ET.fromstring(svg.encode("utf-8"))
        self.assertIn("quoted&quot;name", svg)

    def test_committed_sample_asset_is_valid_svg(self) -> None:
        asset = Path(__file__).resolve().parents[1] / "assets" / "stats.svg"
        self.assertTrue(asset.is_file(), f"missing sample asset: {asset}")
        root = ET.fromstring(asset.read_bytes())
        self.assertEqual(root.tag, f"{{{SVG_NS}}}svg")
        self.assertEqual(root.attrib["width"], "480")
        self.assertEqual(root.attrib["height"], "220")
        text = " ".join(
            element.text or "" for element in root.iter(f"{{{SVG_NS}}}text")
        )
        self.assertIn("Vlad Nikishin", text)
        self.assertNotIn("generated ", text)


if __name__ == "__main__":
    unittest.main()
