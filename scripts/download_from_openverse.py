#!/usr/bin/env python3
"""Download public-domain photos from Openverse into the replacement picture pool.

Openverse (https://openverse.org) indexes openly licensed media from Flickr,
museums and others; this keeps only CC0 and public domain photographs, so no
attribution is required. No API key needed: anonymous use allows 20 requests a
minute and 200 a day, and one request lists up to 20 photos.

The photos come from several search terms in turn (--query, repeatable; a varied
default set otherwise), and are written to the output folder as
  openverse_<id>.jpg  each photo, downscaled to at most --max-side pixels
  openverse.json      id, title, creator, license and source page of every photo
Openverse's mature filter misses things: look through the photos, and list the
ids of rejected ones in openverse.exclude (one per line, # comments) so a rerun
deletes them and fetches replacements instead.
Photos already there count toward --count, so a rerun only fetches what's missing.
"""

import argparse
import io
import json
import logging
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

log = logging.getLogger("download_from_openverse")

API = "https://api.openverse.org/v1/images/"
USER_AGENT = "pixelate-download-from-openverse/0.1 (Python urllib)"
PAGE_SIZE = 20  # the anonymous maximum
LICENSES = "cc0,pdm"
TIMEOUT_S = 60
RETRIES = 3
MIN_SIDE = 200  # skip thumbnails and icons
CREDITS = "openverse.json"
EXCLUDE = "openverse.exclude"
PREFIX = "openverse_"
DEFAULT_QUERIES = [
    "landscape", "city", "food", "animal", "market", "architecture", "flower",
    "ocean", "street", "mountain", "car", "forest", "bird", "building", "river",
]


@dataclass(frozen=True)
class Photo:
    id: str
    title: str
    creator: str
    license: str
    page: str
    url: str


def parse_photos(response: dict) -> list[Photo]:
    """Photos of one API response page; entries missing fields or too small are skipped."""
    photos = []
    for item in response.get("results", []):
        try:
            if min(item.get("width") or MIN_SIDE, item.get("height") or MIN_SIDE) < MIN_SIDE:
                continue
            photo_id = str(item["id"])
            if not photo_id.replace("-", "").isalnum():
                raise ValueError(f"unexpected id {photo_id!r}")
            photos.append(Photo(
                id=photo_id, title=str(item.get("title") or ""), creator=str(item.get("creator") or ""),
                license=str(item["license"]), page=str(item.get("foreign_landing_url") or ""),
                url=str(item["url"]),
            ))
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("skipping malformed result %r: %s", item.get("id"), exc)
    return photos


def get(url: str) -> bytes:
    """GET with retries on rate limits, server errors and network failures."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(1, RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if (exc.code != 429 and exc.code < 500) or attempt == RETRIES:
                raise
            retry_after = exc.headers.get("Retry-After", "")
            wait = int(retry_after) if retry_after.isdigit() else 10 * attempt
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == RETRIES:
                raise
            wait = 5 * attempt
            log.debug("retrying after %s", exc)
        log.warning("%s: retry %d/%d in %ds", url, attempt, RETRIES - 1, wait)
        time.sleep(wait)
    raise AssertionError("unreachable")


def search(query: str) -> Iterator[Photo]:
    """Every CC0 / public domain photograph for query, page by page."""
    page = 1
    while True:
        params = {"q": query, "license": LICENSES, "category": "photograph", "mature": "false",
                  "page_size": PAGE_SIZE, "page": page}
        response = json.loads(get(f"{API}?{urllib.parse.urlencode(params)}"))
        yield from parse_photos(response)
        if page >= int(response.get("page_count") or 0):
            return
        page += 1


def round_robin(queries: list[str]) -> Iterator[Photo]:
    """One photo from each query in turn, until all run dry."""
    active = [search(q) for q in queries]
    while active:
        for results in list(active):
            try:
                yield next(results)
            except StopIteration:
                active.remove(results)


def to_jpeg(data: bytes, max_side: int) -> bytes:
    """Decode, downscale to max_side and re-encode as JPEG; raises ValueError if unreadable."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            image = image.convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError(f"not a readable image: {exc}") from exc
    image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    image.save(out, "JPEG", quality=85, optimize=True)
    return out.getvalue()


def load_credits(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        return {str(c["id"]): c for c in json.loads(path.read_text(encoding="utf-8"))}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log.warning("ignoring unreadable %s: %s", path, exc)
        return {}


def save_credits(path: Path, credits: dict[str, dict]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps([credits[i] for i in sorted(credits)], indent=1, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    tmp.replace(path)


def load_excluded(path: Path) -> set[str]:
    if not path.exists():
        return set()
    lines = (line.split("#", 1)[0].strip() for line in path.read_text(encoding="utf-8").splitlines())
    return {line.removeprefix(PREFIX).removesuffix(".jpg") for line in lines if line}


def download(output: Path, count: int, queries: list[str], max_side: int) -> int:
    """Fill output up to count photos, minus excluded ones; returns how many are there."""
    credits = load_credits(output / CREDITS)
    excluded = load_excluded(output / EXCLUDE)
    for photo_id in excluded:
        (output / f"{PREFIX}{photo_id}.jpg").unlink(missing_ok=True)
        if credits.pop(photo_id, None):
            log.info("removed excluded %s", photo_id)
    have = {p.stem.removeprefix(PREFIX) for p in output.glob(f"{PREFIX}*.jpg")}
    if len(have) >= count:
        save_credits(output / CREDITS, credits)
        log.info("already have %d photos in %s", len(have), output)
        return len(have)
    try:
        for photo in round_robin(queries):
            if photo.id in have or photo.id in excluded:
                continue
            try:
                data = to_jpeg(get(photo.url), max_side)
            except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                log.warning("skipping %s (%s): %s", photo.id, photo.url, exc)
                continue
            target = output / f"{PREFIX}{photo.id}.jpg"
            target.write_bytes(data)
            have.add(photo.id)
            credits[photo.id] = {k: v for k, v in asdict(photo).items() if k != "url"}
            log.info("[%d/%d] %s: %s (%s)", len(have), count, target.name, photo.title[:60], photo.license)
            if len(have) >= count:
                break
    finally:
        save_credits(output / CREDITS, credits)
    return len(have)


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-o", "--output-dir", type=Path,
                        default=Path(__file__).resolve().parent.parent / "data" / "pictures",
                        help="where to write (default: data/pictures)")
    parser.add_argument("-n", "--count", type=int, default=200, help="photos wanted (default: 200)")
    parser.add_argument("-q", "--query", action="append", default=[],
                        help="search term; repeatable (default: a varied built-in set)")
    parser.add_argument("--max-side", type=int, default=1024, help="downscale to this many pixels (default: 1024)")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")
    if args.count < 1 or args.max_side < MIN_SIDE:
        log.error("--count must be positive and --max-side at least %d", MIN_SIDE)
        return 1
    try:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        got = download(args.output_dir, args.count, args.query or DEFAULT_QUERIES, args.max_side)
    except urllib.error.HTTPError as exc:
        log.error("Openverse API error: %s%s", exc, " (rate limited; try again later)" if exc.code == 429 else "")
        return 1
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        log.error("download failed: %s", exc)
        return 1
    if got < args.count:
        log.warning("only %d of %d photos found; add --query terms", got, args.count)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
