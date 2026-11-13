#!/usr/bin/env python3
"""Turning Wikidata's answer into the list file, without the network."""

import unittest

import download_from_wikipedia as d
import list_countries as c


def row(url: str, un: bool) -> dict:
    return {"article": {"value": url}, "un": {"value": "true" if un else "false"}}


RESULTS = {
    "results": {
        "bindings": [
            row("https://en.wikipedia.org/wiki/Gabon", True),
            row("https://en.wikipedia.org/wiki/S%C3%A3o_Tom%C3%A9_and_Pr%C3%ADncipe", True),
            row("https://en.wikipedia.org/wiki/Taiwan", False),
            row("https://en.wikipedia.org/wiki/Gabon", True),
            row("https://en.wikipedia.org/wiki/Austria", True),
            row("https://en.wikipedia.org/wiki/Austria", False),
        ]
    }
}


class ListCountriesTest(unittest.TestCase):
    def test_titles_are_decoded(self):
        self.assertEqual(c.title_of("https://en.wikipedia.org/wiki/C%C3%B4te_d%27Ivoire"), "Côte d'Ivoire")
        with self.assertRaises(ValueError):
            c.title_of("https://en.wikipedia.org/w/index.php?title=X")

    def test_split_dedupes_and_prefers_membership(self):
        members, others = c.split_titles(RESULTS)
        self.assertEqual(members, ["Austria", "Gabon", "São Tomé and Príncipe"])
        self.assertEqual(others, ["Taiwan"])

    def test_output_is_a_valid_download_list(self):
        text = c.render(*c.split_titles(RESULTS))
        parsed = [
            d.parse_article(line.strip(), "en").slug
            for line in text.splitlines()
            if line.strip() and not line.startswith("#")
        ]
        self.assertEqual(parsed, ["austria", "gabon", "sao_tome_and_principe", "taiwan"])


if __name__ == "__main__":
    unittest.main()
