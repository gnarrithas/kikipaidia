#!/usr/bin/env python3
"""Openverse download logic, with the network mocked."""

import io
import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

from PIL import Image

import download_from_openverse as o


def item(i: str, width: int = 800, height: int = 600) -> dict:
    return {"id": i, "title": f"T{i}", "creator": "C", "license": "cc0", "width": width, "height": height,
            "foreign_landing_url": f"https://example.org/{i}", "url": f"https://img.example.org/{i}.jpg"}


def jpeg(width: int, height: int) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), "red").save(out, "JPEG")
    return out.getvalue()


def fake_get(results: dict[str, list[list[dict]]]):
    """results: query -> pages of items. Image URLs ending in bad.jpg return junk."""
    def get(url: str) -> bytes:
        if url.startswith(o.API):
            params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            pages = results[params["q"][0]]
            page = int(params["page"][0])
            return json.dumps({"results": pages[page - 1], "page_count": len(pages)}).encode()
        return b"junk" if url.endswith("bad.jpg") else jpeg(2000, 1000)
    return get


class OpenverseTest(unittest.TestCase):
    def test_parse_skips_malformed_and_small(self):
        photos = o.parse_photos({"results": [item("a"), item("b", 100, 100), {"id": "c"}, item("../x")]})
        self.assertEqual([p.id for p in photos], ["a"])

    def test_round_robin_downscale_and_rerun(self):
        get = mock.Mock(side_effect=fake_get({"x": [[item("x1"), item("bad")], [item("x2")]],
                                              "y": [[item("y1"), item("y2")]]}))
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(o, "get", get):
            out = Path(tmp)
            self.assertEqual(o.download(out, 4, ["x", "y"], 500), 4)
            self.assertEqual(sorted(f.name for f in out.glob("*.jpg")),
                             [f"openverse_{i}.jpg" for i in ("x1", "x2", "y1", "y2")])
            with Image.open(out / "openverse_x1.jpg") as image:
                self.assertEqual(image.size, (500, 250))
            self.assertEqual(len(json.loads((out / o.CREDITS).read_text())), 4)
            calls = get.call_count
            self.assertEqual(o.download(out, 4, ["x", "y"], 500), 4)
            self.assertEqual(get.call_count, calls)

    def test_excluded_are_removed_and_replaced(self):
        get = fake_get({"x": [[item("x1"), item("x2"), item("x3")]]})
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(o, "get", get):
            out = Path(tmp)
            o.download(out, 2, ["x"], 500)
            (out / o.EXCLUDE).write_text("# nope\nopenverse_x1.jpg  # bad\n")
            self.assertEqual(o.download(out, 2, ["x"], 500), 2)
            self.assertEqual(sorted(f.name for f in out.glob("*.jpg")), ["openverse_x2.jpg", "openverse_x3.jpg"])
            self.assertEqual([c["id"] for c in json.loads((out / o.CREDITS).read_text())], ["x2", "x3"])

    def test_runs_out(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(o, "get", fake_get({"x": [[item("x1")]]})):
            self.assertEqual(o.download(Path(tmp), 5, ["x"], 500), 1)


if __name__ == "__main__":
    unittest.main()
