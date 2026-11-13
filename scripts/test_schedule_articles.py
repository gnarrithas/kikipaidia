#!/usr/bin/env python3
"""Checks days are appended only, picked from the unused queue, and failed downloads cleaned up."""

import datetime as dt
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import build_puzzles
import download_from_wikipedia as dl
import schedule_articles as s

SEED = "test-seed"
TITLES = ["Alpha", "Bravo", "Charlie", "Delta", "Echo"]


def day(n: int) -> dt.date:
    return build_puzzles.FIRST_DAY + dt.timedelta(days=n)


def fake_download(article: dl.Article, articles: Path) -> None:
    """What a successful download_from_wikipedia.download leaves behind."""
    articles.mkdir(parents=True, exist_ok=True)
    (articles / f"{article.slug}.html").write_text("<html></html>")
    images = dl.images_dir(articles, article.slug)
    images.mkdir(exist_ok=True)
    (images / dl.COMPLETE).write_text(f"window 16\n{dl.ORIGINALS}\n")


class ScheduleTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.lists = self.root / "lists"
        self.lists.mkdir()
        (self.lists / "a.txt").write_text("# comment\n" + "\n".join(TITLES[:3]) + "\n")
        (self.lists / "b.txt").write_text("Charlie\n" + "\n".join(TITLES[3:]) + "\n")
        self.articles = self.root / "articles"
        self.schedule = self.root / "schedule.txt"
        self.downloads: list[str] = []

    def downloader(self, article: dl.Article, articles: Path) -> None:
        self.downloads.append(article.slug)
        fake_download(article, articles)

    def run_days(self, today: dt.date, downloader=None) -> int:
        return s.schedule_days(
            self.schedule, s.read_queue(self.lists), self.articles, SEED, today, downloader or self.downloader
        )

    def slugs(self) -> list[str]:
        return [slug for _, slug in build_puzzles.read_schedule(self.schedule)]

    def test_queue_spans_lists_without_duplicates(self):
        self.assertEqual([a.slug for a in s.read_queue(self.lists)], ["alpha", "bravo", "charlie", "delta", "echo"])

    def test_fills_every_day_up_to_today_with_distinct_downloaded_articles(self):
        with self.assertLogs(s.log, "WARNING"):  # fewer than LOW_WATER left
            self.assertEqual(self.run_days(day(2)), 3)
        slugs = self.slugs()
        self.assertEqual(len(set(slugs)), 3)
        self.assertEqual(sorted(self.downloads), sorted(slugs))
        self.assertTrue(all(dl.is_done(self.articles, slug, render=False) for slug in slugs))

    def test_appends_only_and_is_idempotent(self):
        with self.assertLogs(s.log, "WARNING"):
            self.run_days(day(0))
            first = self.slugs()
            self.assertEqual(self.run_days(day(0)), 0)
            self.assertEqual(self.slugs(), first)
            self.run_days(day(1))
        self.assertEqual(self.slugs()[:1], first)
        self.assertEqual(len(self.slugs()), 2)

    def test_same_seed_same_schedule(self):
        with self.assertLogs(s.log, "WARNING"):
            self.run_days(day(3))
        first = self.slugs()
        self.schedule.unlink()
        with self.assertLogs(s.log, "WARNING"):
            self.run_days(day(3))
        self.assertEqual(self.slugs(), first)

    def test_downloaded_articles_are_not_downloaded_again(self):
        for title in TITLES:
            fake_download(dl.parse_article(title, "en"), self.articles)
        with self.assertLogs(s.log, "WARNING"):
            self.run_days(day(4))
        self.assertEqual(self.downloads, [])
        self.assertEqual(sorted(self.slugs()), ["alpha", "bravo", "charlie", "delta", "echo"])

    def test_failed_download_is_cleaned_up_and_another_picked(self):
        first = s.pick(s.read_queue(self.lists), SEED, day(0), 0).slug

        def flaky(article: dl.Article, articles: Path) -> None:
            if article.slug == first:
                (articles / f"{article.slug}.html").parent.mkdir(parents=True, exist_ok=True)
                (articles / f"{article.slug}.html").write_text("partial")
                dl.images_dir(articles, article.slug).mkdir()
                raise RuntimeError("1 of 2 pictures failed")
            self.downloader(article, articles)

        with self.assertLogs(s.log, "WARNING"):
            self.run_days(day(0), flaky)
        self.assertNotEqual(self.slugs(), [first])
        self.assertEqual(len(self.slugs()), 1)
        self.assertFalse((self.articles / f"{first}.html").exists())
        self.assertFalse(dl.images_dir(self.articles, first).exists())

    def test_empty_queue_keeps_the_days_before(self):
        with self.assertRaises(s.ScheduleError):
            self.run_days(day(6))
        self.assertEqual(len(self.slugs()), 5)

    def test_empty_seed_is_refused(self):
        with self.assertRaises(ValueError):
            s.schedule_days(self.schedule, [], self.articles, "", day(0), self.downloader)

    def test_main_requires_the_seed(self):
        with mock.patch.dict(os.environ, {build_puzzles.SEED_ENV: ""}), self.assertLogs(s.log, "ERROR"):
            self.assertEqual(s.main(["--lists", str(self.lists), "--schedule", str(self.schedule)]), 1)
        self.assertFalse(self.schedule.exists())


if __name__ == "__main__":
    unittest.main()
