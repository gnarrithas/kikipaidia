#!/usr/bin/env python3
"""Checks the schedule is followed up to today only and nothing leaks."""

import base64
import datetime as dt
import json
import os
import random
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

import build_puzzles as b
import pixelate
import puzzle
import textplay

ROOT = Path(__file__).resolve().parent.parent
ARTICLES = Path(__file__).resolve().parent / "fixtures"
PICTURES = ARTICLES / "pictures"
SLUGS = ["delta", "alpha", "charlie", "bravo", "echo", "foxtrot", "golf", "hotel"]
WORDS = ["apple", "banana", "cherry"]

PAGE = """<html><head><title>Secret Page - Wikipedia</title>
<script>var wgTitle = "Secret Page";</script></head><body>
<nav id="p-lang-btn"><a href="https://fr.wikipedia.org/wiki/Secret">Français</a></nav>
<h1 id="firstHeading"><span>Secret Page</span></h1>
<div id="mw-content-text"><div class="mw-parser-output" lang="en">
<p id="intro" data-mw="Secret_Page"><b>Secret Page</b> is a <a href="/wiki/Thing" title="Thing">thing</a>.</p>
<p>Built in 1990 for the Secret's owner, over the hill.</p>
<div class="mw-heading"><h2 id="Secret_history">History</h2><span class="mw-editsection"><a href="?action=edit">edit</a></span></div>
<img src="//upload.wikimedia.org/Secret.jpg" srcset="//upload.wikimedia.org/Secret2.jpg 2x" width="300" height="200" alt="Secret view">
<span style="background:url(//upload.wikimedia.org/x.png)" onclick="alert(1)">x</span>
<div class="navbox"><a href="/wiki/Secret_topics">Secret topics</a></div>
<!-- parser cache key for Secret_Page -->
<script>leak("Secret")</script>
</div></div>
<div id="catlinks" class="catlinks"><a href="/wiki/Category:Secret">Secret</a></div>
</body></html>
"""


def make_pool(folder: Path, sizes: list[tuple[int, int]]) -> list[puzzle.Picture]:
    for i, (w, h) in enumerate(sizes):
        Image.new("RGB", (w, h), (i * 40, 0, 0)).save(folder / f"p{i}.png")
    return puzzle.load_pictures(folder)


def day(n: int) -> str:
    return (b.FIRST_DAY + dt.timedelta(days=n)).isoformat()


class ScheduleTest(unittest.TestCase):
    def read(self, text: str):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "schedule.txt"
            path.write_text(text)
            return b.read_schedule(path)

    def test_missing_file_is_empty(self):
        self.assertEqual(b.read_schedule(Path("/nonexistent/schedule.txt")), [])

    def test_reads_consecutive_days(self):
        schedule = self.read(f"{day(0)} alpha\n\n{day(1)} bravo\n")
        self.assertEqual(schedule, [(b.FIRST_DAY, "alpha"), (b.FIRST_DAY + dt.timedelta(days=1), "bravo")])

    def test_rejects_gaps_and_malformed_lines(self):
        for text in (f"{day(1)} alpha\n", f"{day(0)} alpha\n{day(2)} bravo\n", f"{day(0)}\n", "x alpha\n"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.read(text)

    def test_daily_slugs_stop_at_today(self):
        schedule = self.read(f"{day(0)} delta\n{day(1)} alpha\n{day(2)} echo\n")
        self.assertEqual(b.daily_slugs(schedule, SLUGS, b.FIRST_DAY), ["delta"])
        self.assertEqual(b.daily_slugs(schedule, SLUGS, b.FIRST_DAY - dt.timedelta(days=9)), [])
        self.assertEqual(b.daily_slugs(schedule, SLUGS, b.FIRST_DAY + dt.timedelta(days=9)), ["delta", "alpha", "echo"])

    def test_publish_all_appends_the_unscheduled_sorted(self):
        schedule = self.read(f"{day(0)} delta\n")
        self.assertEqual(
            b.daily_slugs(schedule, SLUGS, b.FIRST_DAY, publish_all=True),
            ["delta", "alpha", "bravo", "charlie", "echo", "foxtrot", "golf", "hotel"],
        )

    def test_ids_depend_on_the_seed_and_hide_the_slug(self):
        self.assertNotEqual(b.puzzle_id("a", "gabon"), b.puzzle_id("b", "gabon"))
        self.assertRegex(b.puzzle_id("a", "gabon"), r"^[0-9a-f]{16}$")


class PuzzleTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.pool = make_pool(Path(tmp.name), [(30, 20), (20, 30), (10, 10)])

    def build(self) -> puzzle.Puzzle:
        return puzzle.build_puzzle(PAGE, hide_secret, self.pool, random.Random(1))

    def test_title_comes_from_the_heading(self):
        self.assertEqual(self.build().title, "Secret Page")

    def test_nothing_names_the_article(self):
        html = self.build().html
        for leak in ("Secret", "wikimedia", "id=", "data-", "srcset", "lang=", "onclick", "url(",
                     "<script", "<!--", "Français", "edit", "topics"):
            self.assertNotIn(leak, html)

    def test_text_and_text_attributes_are_modified(self):
        html = self.build().html
        self.assertIn('<b><g-w k="2">Hidden</g-w> <g-w k="3">Page</g-w></b>', html)
        self.assertIn('alt="Hidden view"', html)
        self.assertIn('title="Thing"', html)

    def test_starts_with_the_page_title_as_shown(self):
        html = puzzle.build_puzzle(PAGE.replace("<span>Secret Page</span>", "<i>Secret</i> Page"),
                                   hide_secret, self.pool, random.Random(1)).html
        self.assertTrue(html.startswith(
            '<h1 class="firstHeading"><i><g-w k="0">Hidden</g-w></i> <g-w k="1">Page</g-w></h1>'), html[:120])

    def test_links_lead_to_wikipedia_home(self):
        html = self.build().html
        self.assertIn('href="https://www.wikipedia.org/" rel="noopener noreferrer" target="_blank"', html)
        self.assertEqual(html.count("href="), 1)

    def test_pictures_keep_size_and_shape(self):
        built = self.build()
        (picture,) = built.pictures
        self.assertEqual((picture.width, picture.height), (30, 20))
        self.assertIn(f'src="pictures/{picture.name}"', built.html)
        self.assertIn('width="300"', built.html)
        self.assertIn('height="200"', built.html)

    def test_closest_shape_without_a_close_one(self):
        rng = random.Random(0)
        self.assertEqual(puzzle.pick_picture(self.pool, 100, 10, rng).width, 30)
        self.assertEqual(puzzle.pick_picture(self.pool, 10, 100, rng).height, 30)

    def test_rejects_a_page_without_body(self):
        with self.assertRaises(ValueError):
            puzzle.build_puzzle("<h1 id='firstHeading'>X</h1>", hide_secret, self.pool, random.Random(0))

    def test_tokens_follow_the_html_and_get_their_reveal_steps(self):
        built = self.build()
        ks = [int(k) for k in re.findall(r'<g-w k="(\d+)">', built.html)]
        self.assertEqual(ks, list(range(len(built.tokens))))
        steps = {text: step for step, text in built.tokens}
        self.assertEqual(steps["Secret"], puzzle.NEVER)
        self.assertEqual(steps["Page"], puzzle.NEVER)
        self.assertEqual(steps["Secret's"], puzzle.NEVER)
        self.assertEqual(steps["1990"], puzzle.STEP_NUMBERS)
        self.assertEqual(steps["History"], puzzle.STEP_HEADINGS)
        words = [step for step, _ in built.tokens if step == puzzle.HINT_ONLY or step in puzzle.STEP_WORDS]
        for step, share in zip(puzzle.STEP_WORDS, puzzle.WORD_SHARES):
            shown = sum(1 for w in words if w != puzzle.HINT_ONLY and w <= step)
            self.assertEqual(shown, round(len(words) * share))

    def test_pictures_point_at_their_originals(self):
        built = self.build()
        self.assertEqual(built.originals, ["//upload.wikimedia.org/Secret.jpg"])
        self.assertIn('p="0"', built.html)

    def test_bold_names_of_the_first_paragraph_stay_hidden(self):
        page = PAGE.replace("<b>Secret Page</b> is a", "<b>Secret Middle Page</b>, also <b>Alias</b>, is a")
        page = page.replace("over the hill.</p>", "over the hill.</p><p>Middle and Alias, but <b>Bold</b>.</p>")
        page = page.replace('<p id="intro"', '<table><tr><td><p><b>Infobox</b></p></td></tr></table><section><p id="intro"')
        page = page.replace("over the hill.</p>", "over the hill.</p></section>")
        built = puzzle.build_puzzle(page, hide_secret, self.pool, random.Random(1))
        steps = [(text, step) for step, text in built.tokens]
        for name in ("Middle", "Alias"):
            self.assertEqual({step for text, step in steps if text == name}, {puzzle.NEVER})
        self.assertNotEqual(dict(steps)["Bold"], puzzle.NEVER)
        self.assertNotEqual(dict(steps)["also"], puzzle.NEVER)
        self.assertNotEqual(dict(steps)["Infobox"], puzzle.NEVER)

    def test_numbers_of_the_title_and_first_sentence_are_hints_only(self):
        page = PAGE.replace("<span>Secret Page</span>", "<span>Secret Page 7</span>").replace(
            "<b>Secret Page</b> is a", "<b>Secret Page</b> (born 4 May 1980 in St. Ives, U.S.) is a 5").replace(
            "thing</a>.</p>", "thing</a>.<sup>[1]</sup> It has 12 floors.</p>")
        built = puzzle.build_puzzle(page, hide_secret, self.pool, random.Random(1))
        steps = {text: step for step, text in built.tokens}
        self.assertEqual(steps["7"], puzzle.NEVER)
        for number in ("4", "1980", "5"):
            self.assertEqual(steps[number], puzzle.HINT_ONLY, number)
        self.assertEqual(steps["12"], puzzle.STEP_NUMBERS)
        self.assertEqual(steps["1990"], puzzle.STEP_NUMBERS)

    def test_lead_sentence_end(self):
        text = "Kane (born c. 1993 in St. Albans, U.S.) is J. K. a footballer.[1] He plays."
        self.assertEqual(text[: puzzle.lead_sentence_end(text)], text.split("[1]")[0])
        self.assertEqual(puzzle.lead_sentence_end("No full stop"), len("No full stop"))
        self.assertEqual(puzzle.lead_sentence_end("A. Bold. Then.", 3), len("A. Bold."))

    def test_title_terms_skip_stopwords(self):
        self.assertEqual(puzzle.title_terms("The Lord of the Rings"), {"lord", "rings"})
        self.assertEqual(puzzle.title_terms("Anna's Archive"), {"anna", "archive"})
        self.assertEqual(puzzle.title_terms("Digger (2026 film)"), {"digger", "2026", "film"})
        self.assertEqual(puzzle.term("Café’s"), "cafe")


def hide_secret(token: textplay.Token) -> str:
    return token.text.replace("Secret", "Hidden")


class TokenizeTest(unittest.TestCase):
    def test_words_numbers_and_the_rest(self):
        parts = textplay.tokenize("In 1990, a B52 flew 13th at 5 km2 (N)")
        self.assertEqual(
            [(p.kind, p.text) if isinstance(p, textplay.Token) else p for p in parts],
            [("word", "In"), " ", ("number", "1990"), ", ", ("word", "a"), " B52 ", ("word", "flew"), " ",
             ("number", "13th"), " ", ("word", "at"), " ", ("number", "5"), " km2 (N)"],
        )
        self.assertEqual("".join(p if isinstance(p, str) else p.text for p in parts),
                         "In 1990, a B52 flew 13th at 5 km2 (N)")

    def test_replace_token_keeps_capitals(self):
        rng = random.Random(0)
        self.assertEqual(textplay.replace_token(textplay.Token("word", "NASA"), ["moon"], rng), "MOON")
        self.assertRegex(textplay.replace_token(textplay.Token("number", "1990"), ["x"], rng), r"^\d{4}$")


class PixelBlocksTest(unittest.TestCase):
    def test_one_pixel_per_displayed_window(self):
        image = Image.new("RGB", (100, 40))
        self.assertEqual(pixelate.pixel_blocks(image, 32).size, (4, 2))
        self.assertEqual(pixelate.pixel_blocks(image, 32, display_width=50).size, (2, 1))
        self.assertEqual(pixelate.pixel_blocks(Image.new("RGB", (5, 5)), 32).size, (1, 1))

    def test_pixelated_originals_are_copied_by_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            images, out = Path(tmp, "a.images"), Path(tmp, "out")
            images.mkdir()
            out.mkdir()
            Image.new("RGB", (5, 3), "red").save(images / f"{puzzle.image_key('//x/a.jpg')}.png")
            (images / "complete").write_text("window 16\n")
            urls = b.pixelated_originals(images, ["//x/a.jpg", "//x/missing.jpg"], out)
            self.assertEqual(len(urls[0]), len(puzzle.PICTURE_WINDOWS))
            for url in urls[0]:
                self.assertRegex(url, r"^game/pictures/[0-9a-f]{16}\.png$")
            sizes = [Image.open(out / url.rsplit("/", 1)[1]).size for url in urls[0]]
            self.assertEqual(sizes, [(2, 1), (3, 2), (5, 3)])
            self.assertEqual(urls[1], [])

    def test_unpixelated_copy_comes_last(self):
        with tempfile.TemporaryDirectory() as tmp:
            images, out = Path(tmp, "a.images"), Path(tmp, "out")
            images.mkdir()
            out.mkdir()
            key = puzzle.image_key("//x/a.jpg")
            Image.new("RGB", (5, 3), "red").save(images / f"{key}.png")
            Image.new("RGB", (80, 48), "blue").save(images / f"{key}.webp")
            (images / "complete").write_text("window 16\noriginals\n")
            urls = b.pixelated_originals(images, ["//x/a.jpg"], out)
            self.assertEqual(len(urls[0]), len(puzzle.PICTURE_WINDOWS) + 1)
            self.assertRegex(urls[0][-1], r"^game/pictures/[0-9a-f]{16}\.webp$")
            self.assertEqual(Image.open(out / urls[0][-1].rsplit("/", 1)[1]).size, (80, 48))
            self.assertEqual(b.picture_window(images), 16)

    def test_levels_finer_than_the_download_repeat_the_coarser(self):
        with tempfile.TemporaryDirectory() as tmp:
            images, out = Path(tmp, "a.images"), Path(tmp, "out")
            images.mkdir()
            out.mkdir()
            Image.new("RGB", (4, 2), "red").save(images / f"{puzzle.image_key('//x/a.jpg')}.png")
            (images / "complete").write_text("window 32\n")
            with self.assertLogs(b.log, "WARNING"):
                urls = b.pixelated_originals(images, ["//x/a.jpg"], out)
            sizes = [Image.open(out / url.rsplit("/", 1)[1]).size for url in urls[0]]
            self.assertEqual(sizes, [(2, 1), (4, 2), (4, 2)])


class MainTest(unittest.TestCase):
    def setUp(self):
        self.words_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.words_dir.cleanup)
        self.schedule = Path(self.words_dir.name) / "schedule.txt"
        self.schedule.write_text(f"{day(0)} placeholder_gamma\n{day(1)} placeholder_alpha\n{day(2)} placeholder_beta\n")

    def run_main(self, out: Path, seed: str, *extra: str) -> int:
        words = Path(self.words_dir.name) / "words.txt"
        words.write_text("\n".join(WORDS))
        args = [
            str(ARTICLES), str(out), "--pictures", str(PICTURES), "--words", str(words),
            "--schedule", str(self.schedule), "--today", day(1), *extra,
        ]
        with mock.patch.dict(os.environ, {b.SEED_ENV: seed}):
            return b.main(args)

    def test_requires_the_seed(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.run_main(Path(tmp) / "out", ""), 1)
            self.assertFalse((Path(tmp) / "out").exists())

    def test_publishes_up_to_today_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            self.assertEqual(self.run_main(out, "test-seed"), 0)
            ids = out.joinpath("daily.txt").read_text().split()
            self.assertEqual(len(ids), 2)
            self.assertEqual(sorted(p.stem for p in out.glob("puzzles/*.json")), sorted(ids))
            titles = out.joinpath("titles.txt").read_text().splitlines()
            self.assertEqual(titles, ["Placeholder Alpha", "Placeholder Beta", "Placeholder Gamma"])
            data = json.loads(out.joinpath("puzzles", f"{ids[0]}.json").read_text())
            self.assertIn(base64.b64decode(data["answer"]).decode(), titles)
            tokens = json.loads(base64.b64decode(data["tokens"]).decode())
            self.assertEqual(len(tokens), data["html"].count("<g-w "))
            self.assertEqual(len(data["reveals"]), data["html"].count("<img "))
            served = {p.name for p in out.glob("pictures/*")}
            referenced = set(re.findall(r"game/pictures/([^\"]+)", data["html"]))
            self.assertTrue(referenced and referenced <= served)

    def test_follows_the_schedule(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            self.run_main(out, "test-seed")
            self.assertEqual(
                out.joinpath("daily.txt").read_text().split(),
                [b.puzzle_id("test-seed", "placeholder_gamma"), b.puzzle_id("test-seed", "placeholder_alpha")],
            )

    def test_fails_on_an_undownloaded_or_empty_schedule(self):
        for text in (f"{day(0)} placeholder_zulu\n", ""):
            self.schedule.write_text(text)
            with self.subTest(text=text), tempfile.TemporaryDirectory() as tmp, self.assertLogs(b.log, "ERROR"):
                self.assertEqual(self.run_main(Path(tmp), "test-seed"), 1)

    def test_same_seed_same_puzzles(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as c:
            self.run_main(Path(a), "test-seed", "--all")
            self.run_main(Path(c), "test-seed", "--all")
            for path in Path(a).glob("puzzles/*.json"):
                self.assertEqual(path.read_text(), Path(c, "puzzles", path.name).read_text())
            self.assertEqual(len(list(Path(a).glob("puzzles/*.json"))), 3)


if __name__ == "__main__":
    unittest.main()
