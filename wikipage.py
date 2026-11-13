"""Download Wikipedia article HTML and render HTML to an RGBA matrix plus image boxes.

Rendering uses the locally installed Chrome via Playwright. It runs in a worker thread,
so it also works inside Jupyter (where Playwright's sync API can't run on the main
thread's asyncio loop).
"""

import io
import json
import logging
import math
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from PIL import Image
from playwright.sync_api import Page, sync_playwright

from pixelate import RGBA, Box, save_rgba

logger = logging.getLogger(__name__)

CONTENT_SELECTOR = "main#content"
# Chrome cannot capture more than ~16384 px at once; stitch tiles instead.
TILE_HEIGHT = 4000
VIEWPORT_WIDTH = 1200
HIDE_CSS = """
.vector-page-toolbar, .vector-column-end, .vector-page-titlebar-toc,
#p-lang-btn, .vector-sticky-header, #siteNotice, .mw-editsection,
.vector-dropdown, #mw-panel, .vector-column-start { display: none !important; }
"""
IMAGE_RECTS_JS = """
(content) => {
  const c = content.getBoundingClientRect();
  return [...content.querySelectorAll('img')]
    .map(img => img.getBoundingClientRect())
    .filter(r => r.width > 0 && r.height > 0)
    .map(r => ({x: r.left - c.left, y: r.top - c.top, width: r.width, height: r.height}));
}
"""
HEAD_TAG = re.compile(r"<head(\s[^>]*)?>", re.IGNORECASE)
BASE_HREF = re.compile(r'<base\s+href="([^"]+)"', re.IGNORECASE)


@dataclass(frozen=True)
class RenderedPage:
    rgba: RGBA
    boxes: list[Box]


@dataclass(frozen=True)
class ContentRect:
    x: int
    y: int
    width: int
    height: int


def download_html(url: str) -> str:
    """Fetch an article's HTML, with a <base> tag so it renders standalone (CSS, images)."""
    return _in_worker(_download_html, url)


def render_html(html: str) -> RenderedPage:
    """Render article HTML (as from download_html) to RGBA plus visible-image boxes."""
    return _in_worker(_render_html, html)


def save_rendered(page: RenderedPage, stem: Path) -> None:
    """Write <stem>.png and <stem>.images.json (readable by pixelate.load_boxes)."""
    save_rgba(page.rgba, stem.with_name(stem.name + ".png"))
    stem.with_name(stem.name + ".images.json").write_text(
        json.dumps([asdict(b) for b in page.boxes], indent=1) + "\n"
    )


def _in_worker[T, A](fn: Callable[[A], T], arg: A) -> T:
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(fn, arg).result()


def _download_html(url: str) -> str:
    with sync_playwright() as playwright:
        response = playwright.request.new_context().get(url)
        try:
            if not response.ok:
                raise RuntimeError(f"HTTP {response.status} for {url}")
            html = response.text()
            final_url = response.url
        finally:
            response.dispose()
    if not HEAD_TAG.search(html):
        raise RuntimeError(f"no <head> in {url}; not an HTML page?")
    return HEAD_TAG.sub(lambda m: f'{m.group(0)}<base href="{final_url}">', html, count=1)


def _render_html(html: str) -> RenderedPage:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome")
        try:
            page = browser.new_page(viewport={"width": VIEWPORT_WIDTH, "height": TILE_HEIGHT})
            match = BASE_HREF.search(html)
            if match is None:
                raise RuntimeError("HTML has no <base href>; use wikipage.download_html output")
            url = match.group(1)
            # Serve the HTML at the article's real URL: Wikipedia's scripts (collapsed
            # navboxes, map switchers, media players) only initialise on their own origin.
            page.route(
                lambda u: u == url,
                lambda route: route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html),
            )
            page.goto(url, wait_until="networkidle")
            rect = _prepare(page)
            boxes = _image_boxes(page, rect)
            image = _capture_tiles(page, rect)
        finally:
            browser.close()
    return RenderedPage(rgba=np.asarray(image.convert("RGBA"), dtype=np.uint8).copy(), boxes=boxes)


def _prepare(page: Page) -> ContentRect:
    """Hide sidebars/chrome, force lazy images to load, and measure the content box."""
    page.add_style_tag(content=HIDE_CSS)
    page.evaluate("for (const i of document.images) i.loading = 'eager'")
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_timeout(2000)
    page.wait_for_load_state("networkidle")
    page.evaluate("window.scrollTo(0, 0)")
    box = page.locator(CONTENT_SELECTOR).bounding_box()
    if box is None:
        raise RuntimeError(f"{CONTENT_SELECTOR} not found or not visible; not a Wikipedia article?")
    rect = ContentRect(
        x=round(box["x"]), y=round(box["y"]), width=round(box["width"]), height=round(box["height"])
    )
    if rect.width <= 0 or rect.height <= 0:
        raise RuntimeError(f"empty content area: {rect}")
    return rect


def _image_boxes(page: Page, rect: ContentRect) -> list[Box]:
    """Boxes of visible images, clipped to the content area. Page must be at scroll 0."""
    raw: list[dict[str, float]] = page.locator(CONTENT_SELECTOR).evaluate(IMAGE_RECTS_JS)
    boxes: list[Box] = []
    for r in raw:
        box = Box(
            left=max(0, math.floor(r["x"])),
            top=max(0, math.floor(r["y"])),
            right=min(rect.width, math.ceil(r["x"] + r["width"])),
            bottom=min(rect.height, math.ceil(r["y"] + r["height"])),
        )
        if box.right > box.left and box.bottom > box.top:
            boxes.append(box)
    return boxes


def _capture_tiles(page: Page, rect: ContentRect) -> Image.Image:
    """Scroll through the content and stitch viewport screenshots into one image."""
    canvas = Image.new("RGB", (rect.width, rect.height), "white")
    for top in range(0, rect.height, TILE_HEIGHT):
        tile_height = min(TILE_HEIGHT, rect.height - top)
        page.evaluate(f"window.scrollTo(0, {rect.y + top})")
        page.wait_for_timeout(300)
        offset = rect.y + top - page.evaluate("window.scrollY")
        png = page.screenshot(
            clip={"x": rect.x, "y": offset, "width": rect.width, "height": tile_height}
        )
        canvas.paste(Image.open(io.BytesIO(png)).convert("RGB"), (0, top))
        logger.debug("captured rows %d..%d", top, top + tile_height)
    return canvas
