#!/usr/bin/env python3
"""Write the Wikipedia articles of today's sovereign states, for download_from_wikipedia.py.

Asks Wikidata for every item that is currently an instance of "sovereign state"
(Q3624078: no end date on that statement and not dissolved) and has an article
in the chosen Wikipedia. The output lists article titles, one per line, sorted:
UN members first, then the rest under a comment, as Wikidata's tagging of
disputed states (Taiwan, Kosovo, ...) is worth checking by hand.
"""

import argparse
import json
import logging
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ENDPOINT = "https://query.wikidata.org/sparql"
# Wikimedia asks for a descriptive User-Agent; no personal contact details here.
USER_AGENT = "pixelate-list-countries/0.1 (Python urllib)"
TIMEOUT_S = 60

QUERY = """
SELECT DISTINCT ?article ?un WHERE {{
  ?country p:P31 ?statement .
  ?statement ps:P31 wd:Q3624078 .                      # instance of: sovereign state
  FILTER NOT EXISTS {{ ?statement pq:P582 ?ended }}    # ... and still is
  FILTER NOT EXISTS {{ ?country wdt:P576 ?dissolved }}
  ?article schema:about ?country ;
           schema:isPartOf <https://{lang}.wikipedia.org/> .
  BIND(EXISTS {{ ?country wdt:P463 wd:Q1065 }} AS ?un)  # member of: United Nations
}}
"""

log = logging.getLogger("list_countries")


def fetch(lang: str) -> dict:
    """Run the query; returns Wikidata's SPARQL JSON results."""
    url = f"{ENDPOINT}?{urllib.parse.urlencode({'query': QUERY.format(lang=lang), 'format': 'json'})}"
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"}
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        return json.load(response)


def title_of(article_url: str) -> str:
    """https://en.wikipedia.org/wiki/S%C3%A3o_Tom%C3%A9 -> São Tomé."""
    path = urllib.parse.urlparse(article_url).path
    if not path.startswith("/wiki/"):
        raise ValueError(f"not a Wikipedia article URL: {article_url}")
    return urllib.parse.unquote(path.removeprefix("/wiki/")).replace("_", " ")


def split_titles(results: dict) -> tuple[list[str], list[str]]:
    """(UN members, others), each sorted; a title in both counts as a member."""
    members: set[str] = set()
    others: set[str] = set()
    for row in results["results"]["bindings"]:
        title = title_of(row["article"]["value"])
        (members if row.get("un", {}).get("value") == "true" else others).add(title)
    return sorted(members, key=str.casefold), sorted(others - members, key=str.casefold)


def render(members: list[str], others: list[str]) -> str:
    lines = [
        "# Sovereign states with a Wikipedia article, from Wikidata (scripts/list_countries.py).",
        f"# UN members: {len(members)}",
        *members,
    ]
    if others:
        lines += ["", f"# Not UN members, or not tagged as such on Wikidata: {len(others)}; check these", *others]
    return "\n".join(lines) + "\n"


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output", type=Path, help="list file to write")
    parser.add_argument("--lang", default="en", help="Wikipedia language (default: en)")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = parse_args(argv)
    try:
        results = fetch(args.lang)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as err:
        log.error("Wikidata query failed: %s", err)
        return 1
    try:
        members, others = split_titles(results)
    except (KeyError, TypeError, ValueError) as err:
        log.error("unexpected Wikidata response: %s", err)
        return 1
    if not members and not others:
        log.error("Wikidata returned no articles for %s.wikipedia.org", args.lang)
        return 1
    try:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(render(members, others), encoding="utf-8")
    except OSError as err:
        log.error("cannot write %s: %s", args.output, err)
        return 1
    log.info("wrote %d UN members and %d others to %s", len(members), len(others), args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
