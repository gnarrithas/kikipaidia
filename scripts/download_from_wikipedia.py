#!/usr/bin/env python3
"""Download Wikipedia articles: their HTML, optionally with a screenshot of the content.

The input file lists one article per line, as a URL
(https://en.wikipedia.org/wiki/Gabon) or a title (Gabon, Central African Republic).
Blank lines and lines starting with # are ignored.

For each article this writes, in the output directory:
  <slug>.html         the article HTML (with <base>, so it renders standalone);
                      what build_puzzles.py reads
  <slug>.images/      every picture of the article body, pixelated: one pixel per
                      --window x --window block as displayed, named puzzle.image_key(src)
                      .png, and unpixelated (at most twice its displayed size) as
                      <key>.webp, shown once the game is over; `complete` marks a
                      finished download
and, with --render, for the notebook:
  <slug>.png          the content (no sidebars), as rendered
  <slug>.images.json  pixel boxes of every visible image, for pixelate.load_boxes
where <slug> is the lowercased ASCII title with non-alphanumerics replaced by "_"
(e.g. gabon, sao_tome_and_principe).
"""

import argparse
import io
import logging
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

# Shared modules (wikipage, pixelate) live in the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bs4 import BeautifulSoup  # noqa: E402
from PIL import Image, UnidentifiedImageError  # noqa: E402
from playwright.sync_api import Error as PlaywrightError  # noqa: E402

import pixelate  # noqa: E402
import puzzle  # noqa: E402
import wikipage  # noqa: E402

logger = logging.getLogger(__name__)

PAUSE_BETWEEN_ARTICLES_S = 1.0
PAUSE_BETWEEN_IMAGES_S = 0.05
# Wikimedia asks for a descriptive User-Agent; no personal contact details here.
USER_AGENT = "pixelate-download-from-wikipedia/0.1 (Python urllib)"
IMAGE_TIMEOUT_S = 30
IMAGE_RETRIES = 4
COMPLETE = "complete"
# Second line of COMPLETE once the folder also holds the unpixelated <key>.webp copies.
ORIGINALS = "originals"
# Unpixelated copies: at most this many times the displayed width (sharp on high-DPI screens).
ORIGINAL_SCALE = 2
ORIGINAL_QUALITY = 80
# Map tiles are served to Wikimedia sites only (HTTP 403 for anyone else): skipped.
RESTRICTED_HOSTS = frozenset({"maps.wikimedia.org"})


@dataclass(frozen=True)
class Article:
    url: str
    slug: str


def slugify(title: str) -> str:
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^0-9a-z]+", "_", ascii_title.lower()).strip("_")
    if not slug:
        raise ValueError(f"cannot derive a file name from title {title!r}")
    return slug


def parse_article(line: str, lang: str) -> Article:
    """Turn a URL or a bare title into an Article."""
    if line.startswith(("http://", "https://")):
        parsed = urlparse(line)
        if not parsed.netloc.endswith("wikipedia.org") or not parsed.path.startswith("/wiki/"):
            raise ValueError(f"not a Wikipedia article URL: {line}")
        title = unquote(parsed.path.removeprefix("/wiki/"))
        if not title:
            raise ValueError(f"URL has no article title: {line}")
        return Article(url=line, slug=slugify(title.replace("_", " ")))
    url = f"https://{lang}.wikipedia.org/wiki/{quote(line.replace(' ', '_'))}"
    return Article(url=url, slug=slugify(line))


def read_articles(path: Path, lang: str) -> list[Article]:
    """Parse the list file; invalid lines are logged and skipped, duplicate slugs dropped."""
    articles: list[Article] = []
    seen: set[str] = set()
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            article = parse_article(line, lang)
        except ValueError as exc:
            logger.warning("%s:%d: skipping: %s", path, lineno, exc)
            continue
        if article.slug in seen:
            logger.warning("%s:%d: skipping duplicate %s", path, lineno, article.slug)
            continue
        seen.add(article.slug)
        articles.append(article)
    return articles


def outputs(output_dir: Path, slug: str, render: bool) -> list[Path]:
    exts = (".html", ".png", ".images.json") if render else (".html",)
    return [output_dir / f"{slug}{ext}" for ext in exts] + [images_dir(output_dir, slug) / COMPLETE]


def is_done(output_dir: Path, slug: str, render: bool) -> bool:
    """Whether every output exists, the pictures' unpixelated copies included."""
    if not all(p.exists() for p in outputs(output_dir, slug, render)):
        return False
    try:
        marker = (images_dir(output_dir, slug) / COMPLETE).read_text(encoding="utf-8")
    except OSError:
        return False
    return ORIGINALS in marker.split()


def original_copy(image: Image.Image, display_width: int | None) -> Image.Image:
    """The picture unpixelated, at most ORIGINAL_SCALE times its displayed width."""
    image = image.convert("RGBA" if "A" in image.getbands() or "transparency" in image.info else "RGB")
    if display_width and image.width > ORIGINAL_SCALE * display_width:
        height = max(1, round(image.height * ORIGINAL_SCALE * display_width / image.width))
        image = image.resize((ORIGINAL_SCALE * display_width, height), Image.Resampling.LANCZOS)
    return image


def images_dir(output_dir: Path, slug: str) -> Path:
    return output_dir / f"{slug}.images"


def _size(value: object) -> int | None:
    try:
        number = int(str(value))
    except ValueError:
        return None
    return number if number > 0 else None


def fetch_image(url: str) -> Image.Image:
    """Download and decode a picture, waiting and retrying when rate limited (HTTP 429)."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(1, IMAGE_RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=IMAGE_TIMEOUT_S) as response:
                data = response.read()
            break
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == IMAGE_RETRIES:
                raise
            retry_after = exc.headers.get("Retry-After", "")
            wait = int(retry_after) if retry_after.isdigit() else 10 * attempt
            logger.info("rate limited; retrying in %ds", wait)
            time.sleep(wait)
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        return image.copy()


def download_images(html: str, folder: Path, window: int) -> None:
    """Pixelate every picture of the article body into folder (see the module docstring).
    Pictures already there are kept; failures are logged, and leave the folder incomplete."""
    body = BeautifulSoup(html, "html.parser").select_one(puzzle.BODY_SELECTOR)
    if body is None:
        raise RuntimeError(f"{puzzle.BODY_SELECTOR} not found; not an article page?")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / COMPLETE).unlink(missing_ok=True)
    seen: set[str] = set()
    failed = 0
    for img in body.find_all("img"):
        src = str(img.get("src") or "")
        if not src or src in seen:
            continue
        seen.add(src)
        key = puzzle.image_key(src)
        target, original = folder / f"{key}.png", folder / f"{key}.webp"
        if target.exists() and original.exists():
            continue
        url = f"https:{src}" if src.startswith("//") else src
        if urlparse(url).hostname in RESTRICTED_HOSTS:
            logger.info("skipping restricted picture %s", url)
            continue
        width = _size(img.get("width"))
        try:
            image = fetch_image(url)
            if not target.exists():
                pixelate.pixel_blocks(image, window, width).save(target, format="PNG")
            original_copy(image, width).save(original, format="WEBP", quality=ORIGINAL_QUALITY, method=6)
        except (urllib.error.URLError, TimeoutError, OSError, UnidentifiedImageError, ValueError) as exc:
            logger.warning("picture %s failed: %s", url, exc)
            failed += 1
        time.sleep(PAUSE_BETWEEN_IMAGES_S)
    if failed:
        raise RuntimeError(f"{failed} of {len(seen)} pictures failed; run again to retry them")
    (folder / COMPLETE).write_text(f"window {window}\n{ORIGINALS}\n", encoding="utf-8")
    logger.info("pixelated %d pictures into %s", len(seen), folder)


def download(article: Article, output_dir: Path, render: bool, window: int, force: bool) -> None:
    page = output_dir / f"{article.slug}.html"
    if force or not page.exists():
        html = wikipage.download_html(article.url)
        page.write_text(html, encoding="utf-8")
    else:
        html = page.read_text(encoding="utf-8")
    download_images(html, images_dir(output_dir, article.slug), window)
    if not render:
        logger.info("saved %s.html", output_dir / article.slug)
        return
    rendered = wikipage.render_html(html)
    wikipage.save_rendered(rendered, output_dir / article.slug)
    height, width = rendered.rgba.shape[:2]
    logger.info("saved %s.{html,png,images.json} (%dx%d, %d image boxes)",
                output_dir / article.slug, width, height, len(rendered.boxes))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("articles", type=Path, help="text file, one article URL or title per line")
    parser.add_argument(
        "-o", "--output-dir", type=Path, default=Path("."), help="where to write files (default: .)"
    )
    parser.add_argument("--lang", default="en", help="Wikipedia language for bare titles (default: en)")
    parser.add_argument(
        "--render", action="store_true", help="also write <slug>.png and <slug>.images.json"
    )
    parser.add_argument(
        "--window", type=int, default=16,
        help="pixelation block size in displayed pixels (default: 16, the game's finest; it makes"
        " coarser ones from these); delete <slug>.images/ to redo",
    )
    parser.add_argument("--force", action="store_true", help="re-download articles that already exist")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    if args.window < 1:
        logger.error("--window must be at least 1")
        return 1
    try:
        articles = read_articles(args.articles, args.lang)
    except (OSError, UnicodeDecodeError) as exc:
        logger.error("cannot read %s: %s", args.articles, exc)
        return 1
    if not articles:
        logger.error("no articles found in %s", args.articles)
        return 1

    output_dir: Path = args.output_dir
    todo = [
        a for a in articles
        if args.force or not is_done(output_dir, a.slug, args.render)
    ]
    if skipped := len(articles) - len(todo):
        logger.info("skipping %d already downloaded article(s); use --force to redo", skipped)
    if not todo:
        return 0
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.error("cannot create %s: %s", output_dir, exc)
        return 1

    failed: list[str] = []
    for i, article in enumerate(todo, start=1):
        logger.info("[%d/%d] %s", i, len(todo), article.url)
        try:
            download(article, output_dir, args.render, args.window, args.force)
        except (PlaywrightError, RuntimeError, OSError, UnicodeDecodeError) as exc:
            logger.error("failed %s: %s", article.url, exc)
            failed.append(article.url)
        if i < len(todo):
            time.sleep(PAUSE_BETWEEN_ARTICLES_S)

    if failed:
        logger.error("%d of %d article(s) failed: %s", len(failed), len(todo), ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
