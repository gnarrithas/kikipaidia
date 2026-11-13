#!/usr/bin/env python3
"""Schedule an article for every day up to today, downloading the ones it picks.

The queue is every title in data/lists/*.txt (download_from_wikipedia.py's
format). Each day not yet in the schedule (data/schedule.txt, line N
"<date> <slug>" for day N, day 0 being build_puzzles.FIRST_DAY) gets a queued
article never scheduled before, picked with HMAC-SHA256 of the secret seed and
the date, so the order can't be guessed from the public lists. Picks not yet
downloaded are downloaded into data/articles/; one that fails is cleaned up
and another picked. Lines are only ever appended, so published days never
change. Deploys run this daily, before build_puzzles.py, and commit the result.

Exits 1 when a day can't be scheduled (the queue ran dry, or every download
failed); the days scheduled before it are kept. Warns when fewer than
--low-water queued titles are left.
"""

import argparse
import datetime as dt
import logging
import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_puzzles  # noqa: E402
import download_from_wikipedia as dl  # noqa: E402

ROOT = build_puzzles.ROOT
WINDOW = 16  # download_from_wikipedia.py's default block size, the game's finest
LOW_WATER = 14
# Attempts per day before giving up (each failed download drops a candidate).
MAX_ATTEMPTS = 5

log = logging.getLogger("schedule_articles")

Downloader = Callable[[dl.Article, Path], None]


class ScheduleError(Exception):
    pass


def warn(message: str, *args) -> None:
    """Log a warning, also as a GitHub Actions annotation when running there."""
    log.warning(message, *args)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print("::warning::" + (message % args), flush=True)


def read_queue(lists: Path, lang: str = "en") -> list[dl.Article]:
    """Every article of lists/*.txt, by file name then line, duplicate slugs dropped."""
    queue: list[dl.Article] = []
    seen: set[str] = set()
    for path in sorted(lists.glob("*.txt")):
        for article in dl.read_articles(path, lang):
            if article.slug not in seen:
                seen.add(article.slug)
                queue.append(article)
    return queue


def pick(candidates: list[dl.Article], seed: str, date: dt.date, attempt: int) -> dl.Article:
    """The candidate for date's attempt, by HMAC of the seed (candidates sorted by slug first)."""
    ordered = sorted(candidates, key=lambda a: a.slug)
    mac = build_puzzles._mac(seed, "pick", f"{date.isoformat()}:{attempt}")
    return ordered[int.from_bytes(mac, "big") % len(ordered)]


def download(article: dl.Article, articles: Path) -> None:
    dl.download(article, articles, render=False, window=WINDOW, force=False)


def ensure_downloaded(article: dl.Article, articles: Path, downloader: Downloader) -> bool:
    """Whether article is (now) fully downloaded; a failed download leaves nothing new behind."""
    if dl.is_done(articles, article.slug, render=False):
        return True
    page, images = articles / f"{article.slug}.html", dl.images_dir(articles, article.slug)
    had_page, had_images = page.exists(), images.exists()
    articles.mkdir(parents=True, exist_ok=True)
    try:
        downloader(article, articles)
        if dl.is_done(articles, article.slug, render=False):
            return True
        warn("download of a queued article left it incomplete; picking another")
    except (dl.PlaywrightError, RuntimeError, OSError, UnicodeDecodeError) as exc:
        warn("download of a queued article failed (%s); picking another", exc)
    if not had_page:
        page.unlink(missing_ok=True)
    if not had_images:
        shutil.rmtree(images, ignore_errors=True)
    return False


def schedule_days(
    schedule_path: Path, queue: list[dl.Article], articles: Path, seed: str, today: dt.date,
    downloader: Downloader = download,
) -> int:
    """Append a line for every unscheduled day up to today; returns how many were added.
    Raises ScheduleError when a day can't be filled (earlier days stay appended)."""
    if not seed:
        raise ValueError("the seed is empty")
    schedule = build_puzzles.read_schedule(schedule_path)
    used = {slug for _, slug in schedule}
    candidates = [a for a in queue if a.slug not in used]
    added = 0
    for day in range(len(schedule), (today - build_puzzles.FIRST_DAY).days + 1):
        date = build_puzzles.FIRST_DAY + dt.timedelta(days=day)
        chosen = None
        for attempt in range(MAX_ATTEMPTS):
            if not candidates:
                raise ScheduleError(f"the queue is empty; add titles to data/lists/ to schedule {date}")
            article = pick(candidates, seed, date, attempt)
            if ensure_downloaded(article, articles, downloader):
                chosen = article
                break
            candidates.remove(article)
        if chosen is None:
            raise ScheduleError(f"{MAX_ATTEMPTS} downloads failed for {date}")
        schedule_path.parent.mkdir(parents=True, exist_ok=True)
        with schedule_path.open("a", encoding="utf-8") as out:
            out.write(f"{date.isoformat()} {chosen.slug}\n")
        candidates.remove(chosen)
        added += 1
    if len(candidates) < LOW_WATER:
        warn("only %d queued articles left; add titles to data/lists/", len(candidates))
    return added


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0], formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--lists", type=Path, default=ROOT / "data" / "lists",
        help="folder of queued title lists, *.txt (default: data/lists)",
    )
    parser.add_argument(
        "--articles", type=Path, default=ROOT / "data" / "articles",
        help="downloaded articles (default: data/articles)",
    )
    parser.add_argument(
        "--schedule", type=Path, default=ROOT / "data" / "schedule.txt",
        help="day-by-day schedule to append to (default: data/schedule.txt)",
    )
    parser.add_argument("--today", type=build_puzzles.parse_date, help="last UTC date to schedule (default: today)")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = parse_args(argv)
    # Read from the environment so the secret never shows in argv or logs.
    seed = os.environ.get(build_puzzles.SEED_ENV, "")
    if not seed:
        log.error("%s is not set or empty", build_puzzles.SEED_ENV)
        return 1
    today = args.today or dt.datetime.now(dt.timezone.utc).date()
    try:
        queue = read_queue(args.lists)
        added = schedule_days(args.schedule, queue, args.articles, seed, today)
    except (OSError, ValueError, ScheduleError) as err:
        log.error("%s", err)
        return 1
    # Only counts: which article is which day stays out of the logs.
    log.info("scheduled %d new day(s) up to %s UTC", added, today)
    return 0


if __name__ == "__main__":
    sys.exit(main())
