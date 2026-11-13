#!/usr/bin/env python3
"""Write the most viewed Wikipedia articles, for download_from_wikipedia.py.

Reads a Wikimedia pageviews "top" response, e.g. saved from
https://wikimedia.org/api/rest_v1/metrics/pageviews/top/en.wikipedia.org/all-access/2026/10/all-days
and writes the top N main-namespace articles as titles, one per line, by rank.
The main page and other namespaces (Special:, Wikipedia:, File:, ...) are skipped.
"""

import argparse
import json
import logging
import sys
from pathlib import Path

log = logging.getLogger("top_articles")

MAIN_PAGES = {"main_page"}
# Non-article namespaces of English Wikipedia (and their talk pages), lowercased.
NAMESPACES = {
    "talk", "user", "user talk", "wikipedia", "wikipedia talk", "wp", "file", "file talk",
    "image", "mediawiki", "mediawiki talk", "template", "template talk", "help", "help talk",
    "category", "category talk", "portal", "portal talk", "draft", "draft talk",
    "timedtext", "timedtext talk", "module", "module talk", "special", "media",
}


def is_article(title: str) -> bool:
    normalized = title.replace("_", " ").strip().casefold()
    if not normalized or normalized.replace(" ", "_") in MAIN_PAGES:
        return False
    prefix, sep, _ = normalized.partition(":")
    return not (sep and prefix.strip() in NAMESPACES)


def top_titles(response: dict, count: int) -> list[str]:
    """Titles of the `count` best ranked articles, spaces for underscores, no duplicates."""
    rows = [row for item in response["items"] for row in item["articles"]]
    titles: list[str] = []
    seen: set[str] = set()
    for row in sorted(rows, key=lambda r: r["rank"]):
        title = row["article"].replace("_", " ")
        if not is_article(title) or title.casefold() in seen:
            continue
        seen.add(title.casefold())
        titles.append(title)
        if len(titles) == count:
            break
    return titles


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("pageviews", type=Path, help="saved pageviews top JSON")
    parser.add_argument("-n", "--count", type=int, default=100, help="articles to keep (default: 100)")
    parser.add_argument("-o", "--output", type=Path, help="list file to write (default: stdout)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.count < 1:
        log.error("--count must be positive")
        return 1
    try:
        response = json.loads(args.pageviews.read_text(encoding="utf-8"))
        titles = top_titles(response, args.count)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        log.error("cannot read %s: %s", args.pageviews, exc)
        return 1
    except (KeyError, TypeError) as exc:
        log.error("%s is not a pageviews top response: missing %s", args.pageviews, exc)
        return 1
    if len(titles) < args.count:
        log.warning("only %d articles available, asked for %d", len(titles), args.count)
    text = f"# Top {len(titles)} articles from {args.pageviews.name} (scripts/top_articles.py).\n"
    text += "".join(f"{t}\n" for t in titles)
    if args.output is None:
        sys.stdout.write(text)
        return 0
    try:
        args.output.write_text(text, encoding="utf-8")
    except OSError as exc:
        log.error("cannot write %s: %s", args.output, exc)
        return 1
    log.info("wrote %d titles to %s", len(titles), args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
