#!/usr/bin/env python3
"""Write the published puzzles: the shuffled articles, scrambled, up to today only.

Deploys run this daily after the build. It reads the downloaded article pages
(<slug>.html, and their pictures in <slug>.images/, pixelated and unpixelated,
from scripts/download_from_wikipedia.py), shuffles them with the secret seed, and
writes into OUTPUT:
  daily.txt          line N is day N's puzzle id, day 0 being FIRST_DAY
  puzzles/<id>.json  {"answer": base64 of the title, "html": the scrambled body,
                      "tokens": base64 of [[reveal step, original text], ...] by <g-w k>,
                      "reveals": URLs of each <img p>'s original pixelated in each of
                                 puzzle.PICTURE_WINDOWS (coarsest first), then
                                 unpixelated if downloaded (shown once the game
                                 is over), [] if none}
  pictures/<name>    the pictures those puzzles show, named by content hash
  titles.txt         every article's title, sorted: the guesses the game offers
Days stop at today (UTC), so tomorrow's puzzle is never published; --all
publishes every article (development). Puzzle ids and each puzzle's random
words, numbers and pictures come from HMAC-SHA256 of the seed and the slug, so
ids can't be guessed and a puzzle is the same in every deploy.

The shuffle uses HMAC-SHA256 in counter mode and an exact rejection sampler,
never Python's `random` module, so it gives the same order on any Python
version. The scrambling does use `random` (seeded), as textplay does.
"""

import argparse
import base64
import datetime as dt
import hashlib
import hmac
import io
import json
import logging
import math
import os
import random
import shutil
import sys
from pathlib import Path

from PIL import Image

# Shared modules (puzzle, textplay) live in the repository root.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import puzzle  # noqa: E402
import textplay  # noqa: E402

SEED_ENV = "SOLUTIONS_SHUFFLE_SEED"
# Must match FIRST_DAY_UTC in src/data/daily.ts.
FIRST_DAY = dt.date(2026, 11, 12)
# Where the game serves OUTPUT from, relative to the site root; must match src/data/daily.ts.
SERVED_AT = "game/"
ID_LENGTH = 16
# Block size of picture folders without a `complete` marker naming theirs (download_from_wikipedia.py's).
DEFAULT_WINDOW = 32

log = logging.getLogger("build_puzzles")


class ByteStream:
    """Deterministic 64-bit integers: HMAC-SHA256(seed, counter) blocks."""

    def __init__(self, seed: bytes):
        self._seed = seed
        self._counter = 0
        self._buffer = b""

    def next_u64(self) -> int:
        if len(self._buffer) < 8:
            block = hmac.new(
                self._seed, self._counter.to_bytes(8, "big"), hashlib.sha256
            ).digest()
            self._counter += 1
            self._buffer += block
        value, self._buffer = self._buffer[:8], self._buffer[8:]
        return int.from_bytes(value, "big")

    def below(self, n: int) -> int:
        """Uniform integer in [0, n), without modulo bias."""
        if n <= 0:
            raise ValueError(f"n must be positive, got {n}")
        limit = (1 << 64) - ((1 << 64) % n)
        while True:
            value = self.next_u64()
            if value < limit:
                return value % n


def shuffle(slugs, seed: str) -> list[str]:
    """Fisher-Yates shuffle of the sorted, deduplicated slugs, driven by the seed."""
    if not seed:
        raise ValueError("the seed is empty")
    order = sorted(set(slugs))
    stream = ByteStream(seed.encode("utf-8"))
    for i in range(len(order) - 1, 0, -1):
        j = stream.below(i + 1)
        order[i], order[j] = order[j], order[i]
    return order


def daily_slugs(slugs, seed: str, today: dt.date, publish_all: bool = False) -> list[str]:
    """Slugs for day 0 through today (or every slug, if more); the shuffle wraps."""
    order = shuffle(slugs, seed)
    if not order:
        return []
    last_day = max(0, (today - FIRST_DAY).days)
    if publish_all:
        last_day = max(last_day, len(order) - 1)
    return [order[day % len(order)] for day in range(last_day + 1)]


def _mac(seed: str, purpose: str, slug: str) -> bytes:
    return hmac.new(seed.encode("utf-8"), f"{purpose}:{slug}".encode("utf-8"), hashlib.sha256).digest()


def puzzle_id(seed: str, slug: str) -> str:
    return _mac(seed, "id", slug).hex()[:ID_LENGTH]


def scramble_rng(seed: str, slug: str) -> random.Random:
    return random.Random(int.from_bytes(_mac(seed, "scramble", slug), "big"))


def make_puzzle(html: str, words: list[str], pictures: list[puzzle.Picture], rng: random.Random) -> puzzle.Puzzle:
    return puzzle.build_puzzle(
        html, lambda token: textplay.replace_token(token, words, rng), pictures, rng,
        picture_url=f"{SERVED_AT}pictures/",
    )


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def puzzle_json(p: puzzle.Puzzle, reveals: list[str]) -> str:
    # Base64 keeps the answer and the real text from showing at a glance in the network tab;
    # they are no secret.
    tokens = json.dumps([list(t) for t in p.tokens], ensure_ascii=False, separators=(",", ":"))
    return json.dumps(
        {"answer": _b64(p.title), "html": p.html, "tokens": _b64(tokens), "reveals": reveals},
        ensure_ascii=False,
    )


def picture_window(images: Path) -> int:
    """The block size images/ was pixelated with, from download_from_wikipedia.py's `complete` marker."""
    try:
        lines = (images / "complete").read_text(encoding="utf-8").splitlines()
    except OSError:
        return DEFAULT_WINDOW
    words = lines[0].split() if lines else []
    if len(words) == 2 and words[0] == "window" and words[1].isdigit() and int(words[1]) > 0:
        return int(words[1])
    log.warning("%s/complete is malformed; assuming window %d", images, DEFAULT_WINDOW)
    return DEFAULT_WINDOW


def coarser(blocks: Image.Image, factor: int) -> Image.Image:
    """Pixel blocks factor times bigger: one pixel per factor x factor of them, averaged."""
    size = (math.ceil(blocks.width / factor), math.ceil(blocks.height / factor))
    return blocks.convert("RGBA").resize(size, Image.Resampling.BOX) if factor > 1 else blocks


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _publish(data: bytes, suffix: str, output: Path) -> str:
    """Write data to output named by content hash; returns its URL."""
    name = hashlib.sha256(data).hexdigest()[:16] + suffix
    if not (output / name).exists():
        (output / name).write_bytes(data)
    return f"{SERVED_AT}pictures/{name}"


def pixelated_originals(images: Path, originals: list[str], output: Path) -> list[list[str]]:
    """Write each original pixelated in each of puzzle.PICTURE_WINDOWS to output, named by
    content hash, from its blocks in images/<key>.png, then its unpixelated images/<key>.webp
    if there is one; returns their URLs by original, [] where there are no blocks. A window
    finer than images' (or no multiple of it) gets the next coarser one."""
    window = picture_window(images)
    levels = [w // window if w % window == 0 else None for w in puzzle.PICTURE_WINDOWS]
    if None in levels:
        log.warning(
            "%s holds %dpx blocks: no %s px pictures; download them again with --window %d",
            images, window, "/".join(str(w) for w, f in zip(puzzle.PICTURE_WINDOWS, levels) if f is None),
            math.gcd(*puzzle.PICTURE_WINDOWS),
        )
    urls: list[list[str]] = []
    for src in originals:
        path = images / f"{puzzle.image_key(src)}.png"
        if not path.is_file():
            urls.append([])
            continue
        with Image.open(path) as blocks:
            blocks.load()
            made = [None if f is None else _png(coarser(blocks, f)) for f in levels]
        # Missing levels repeat the one before (coarser); a missing first one, the first there is.
        first = next((data for data in made if data is not None), None)
        if first is None:
            urls.append([])
            continue
        level_urls = []
        last = first
        for data in made:
            last = data if data is not None else last
            level_urls.append(_publish(last, ".png", output))
        unpixelated = path.with_suffix(".webp")
        if unpixelated.is_file():
            level_urls.append(_publish(unpixelated.read_bytes(), ".webp", output))
        urls.append(level_urls)
    return urls


def build(
    articles: Path, output: Path, words: list[str], pictures: list[puzzle.Picture],
    seed: str, today: dt.date, publish_all: bool,
) -> int:
    """Write OUTPUT (see the module docstring); returns the number of published days."""
    pages = {path.stem: path for path in sorted(articles.glob("*.html"))}
    if not pages:
        raise ValueError(f"no .html articles in {articles}")
    titles = sorted(
        {puzzle.page_title(p.read_text(encoding="utf-8")) for p in pages.values()},
        key=str.casefold,
    )
    days = daily_slugs(pages, seed, today, publish_all)

    # Both are this script's own output; rebuilt from scratch so no stale puzzle stays published.
    for sub in ("puzzles", "pictures"):
        shutil.rmtree(output / sub, ignore_errors=True)
        (output / sub).mkdir(parents=True)
    used: set[puzzle.Picture] = set()
    for slug in sorted(set(days)):
        built = make_puzzle(pages[slug].read_text(encoding="utf-8"), words, pictures, scramble_rng(seed, slug))
        reveals = pixelated_originals(articles / f"{slug}.images", built.originals, output / "pictures")
        if built.originals and not any(reveals):
            log.warning("no pixelated pictures for an article; run download_from_wikipedia.py again")
        (output / "puzzles" / f"{puzzle_id(seed, slug)}.json").write_text(
            puzzle_json(built, reveals), encoding="utf-8"
        )
        used |= built.pictures
    for picture in used:
        shutil.copyfile(picture.path, output / "pictures" / picture.name)
    (output / "titles.txt").write_text("\n".join(titles) + "\n", encoding="utf-8")
    (output / "daily.txt").write_text(
        "\n".join(puzzle_id(seed, slug) for slug in days) + "\n", encoding="utf-8"
    )
    return len(days)


def parse_date(text: str) -> dt.date:
    try:
        return dt.date.fromisoformat(text)
    except ValueError as err:
        raise argparse.ArgumentTypeError(f"not a YYYY-MM-DD date: {text!r}") from err


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0], formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("articles", type=Path, help="folder of downloaded <slug>.html pages")
    parser.add_argument("output", type=Path, help="folder to write (served at /game/)")
    parser.add_argument(
        "--pictures", type=Path, default=ROOT / "data" / "pictures",
        help="replacement picture pool (default: data/pictures)",
    )
    parser.add_argument(
        "--words", type=Path, default=ROOT / "words.txt",
        help="replacement words, one per line (default: words.txt)",
    )
    parser.add_argument("--today", type=parse_date, help="last UTC date to publish (default: today)")
    parser.add_argument("--all", action="store_true", help="publish every article (development)")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = parse_args(argv)
    # Read from the environment so the secret never shows in argv or logs.
    seed = os.environ.get(SEED_ENV, "")
    if not seed:
        log.error("%s is not set or empty", SEED_ENV)
        return 1
    try:
        words = textplay.load_words(args.words)
        pictures = puzzle.load_pictures(args.pictures)
    except (OSError, ValueError) as err:
        log.error("%s", err)
        return 1
    today = args.today or dt.datetime.now(dt.timezone.utc).date()
    try:
        days = build(args.articles, args.output, words, pictures, seed, today, args.all)
    except (OSError, ValueError) as err:
        log.error("%s", err)
        return 1
    # Only counts: which article is which day is the secret.
    log.info("wrote days 0-%d (as of %s UTC) to %s", days - 1, today, args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
