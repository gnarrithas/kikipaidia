#!/usr/bin/env python3
"""Picking the top articles out of a pageviews response."""

import unittest

import top_articles as t


def response(*titles: str) -> dict:
    rows = [{"article": a, "views": 100 - i, "rank": i + 1} for i, a in enumerate(titles)]
    return {"items": [{"articles": list(reversed(rows))}]}


class TopArticlesTest(unittest.TestCase):
    def test_skips_non_articles_and_keeps_rank_order(self):
        r = response("Main_Page", "Special:Search", "Christa_Pike", "Wikipedia:About",
                     "File:WhatsApp.svg", "special:search", "Avengers:_Doomsday", "Help:IPA/English")
        self.assertEqual(t.top_titles(r, 10), ["Christa Pike", "Avengers: Doomsday"])

    def test_count_and_duplicates(self):
        r = response("A", "a", "B", "C")
        self.assertEqual(t.top_titles(r, 2), ["A", "B"])


if __name__ == "__main__":
    unittest.main()
