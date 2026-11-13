"""Turn a downloaded Wikipedia article into a puzzle: its body only, with nothing naming it.

build_puzzle keeps the article body (no tabs, menus, search, language links, contents
sidebar, categories or footer), headed by the page title as Wikipedia shows it
(<h1 class="firstHeading">, italics included), and:
  - drops scripts, comments, embedded media and navigation-only blocks (navboxes, edit links);
  - points every link at https://www.wikipedia.org/;
  - keeps only presentational attributes (class, style, sizes); ids, data-*, srcset,
    lang and the like go, as they can spell out titles;
  - replaces every word and number of the text by `replace`'s stand-in, wrapped in
    <g-w k="N">, and records the original with the step that reveals it (Puzzle.tokens);
  - scrambles alt/title/aria-label text the same way, for good;
  - replaces every picture by a random pool picture of a similar shape, tagged p="N"
    for its original (Puzzle.originals), which the game shows pixelated, ever finer.

Reveal steps: the game shows the real text of token N once it reaches its step, in order:
  STEP_NUMBERS        every number
  (2)                 the original pictures, pixelated in PICTURE_WINDOWS[0] blocks
  STEP_WORDS[0]       a random WORD_SHARES[0] of the other words
  (4)                 the pictures in PICTURE_WINDOWS[1] blocks
  STEP_WORDS[1]       more of them, WORD_SHARES[1] in all
  STEP_HEADINGS       the words of section headings
  (7)                 the pictures in PICTURE_WINDOWS[2] blocks
  STEP_WORDS[2]       more again, WORD_SHARES[2] in all
The picture steps (PICTURE_STEPS) reveal no text; the game knows them by number.
HINT_ONLY tokens show only when the player spends a hint on them; NEVER tokens, the
words of the title and of the bold name(s) opening the article (lead_names), except
STOPWORDS, not even then. Numbers of the article's first sentence (lead_sentence_end),
which tend to give it away (dates of birth, years), are HINT_ONLY instead of STEP_NUMBERS
and no word step shows them. Everything shows once the game ends.
"""

import hashlib
import logging
import math
import random
import re
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup, Comment, NavigableString, Tag
from PIL import Image, UnidentifiedImageError

import textplay

logger = logging.getLogger(__name__)

BODY_SELECTOR = "#mw-content-text .mw-parser-output"
LINK_TARGET = "https://www.wikipedia.org/"
DROP_TAGS = [
    "script", "noscript", "link", "meta", "base", "template", "iframe", "object", "embed",
    "form", "input", "button", "select", "textarea", "audio", "video", "source", "track",
    "map", "area",
]
DROP_SELECTORS = [
    ".mw-editsection",  # [edit] links
    ".navbox", ".navbox-styles", ".vertical-navbox",  # link farms, collapsed by JS on Wikipedia
    ".noprint", ".metadata",  # Wikipedia's own "not part of the article" markers
    ".shortdescription", ".mw-empty-elt", ".Z3988",  # hidden metadata (Z3988: citation COinS)
    ".authority-control", ".sistersitebox", ".portalbox", ".side-box",
    ".catlinks", "#coordinates", ".mw-jump-link",
]
KEEP_ATTRS = frozenset(
    {"class", "style", "width", "height", "colspan", "rowspan", "scope", "align", "valign", "dir"}
)
TEXT_ATTRS = frozenset({"alt", "title", "aria-label"})
PICTURE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})
# Pictures whose aspect ratio is within this factor of the original's count as "same shape".
ASPECT_TOLERANCE = 1.15

NEVER, HINT_ONLY, STEP_NUMBERS, STEP_HEADINGS = -1, 0, 1, 6
# Must match src/game.ts: the steps showing the pictures, and the block size (in displayed
# pixels) each shows them in.
PICTURE_STEPS = (2, 4, 7)
PICTURE_WINDOWS = (64, 32, 16)
# The steps revealing words, and the share of the (non-heading, non-title) words shown by each.
STEP_WORDS = (3, 5, 8)
WORD_SHARES = (0.10, 0.25, 0.50)
HEADINGS = ["h1", "h2", "h3", "h4", "h5", "h6"]
# Words of the title that the game may still reveal: they give nothing away.
STOPWORDS = frozenset("""
a an the and or nor but of in on at to for from by with without over under into onto upon
about above across after against along among around as before behind below beneath beside
between beyond during except inside near off out outside since than through throughout till
toward towards until up via within is are was were be it its this that de la le les du des
del der den die das von van y et el
""".split())
TOKEN_TAG = "g-w"
# A full stop ending a sentence: followed by space, a footnote mark or the end, then no lowercase.
SENTENCE_END = re.compile(r"[.!?](?=(?:\[[^\]]*\])*(?:\s+[^\sa-z]|\s*$))")
# Words a full stop follows without ending the sentence.
ABBREVIATIONS = frozenset("c ca jr sr dr mr mrs ms st mt no vs inc ltd co corp bros".split())


@dataclass(frozen=True)
class Picture:
    path: Path
    width: int
    height: int
    name: str
    """Served file name: a content hash, so it says nothing about the picture."""


@dataclass(frozen=True)
class Puzzle:
    title: str
    html: str
    """Inner HTML of the article body."""
    pictures: frozenset[Picture]
    """Pool pictures the HTML uses."""
    tokens: list[tuple[int, str]]
    """(reveal step, original text) of each <g-w k="N">, by N."""
    originals: list[str]
    """Original src of each <img p="N">, by N."""


def term(word: str) -> str:
    """A word as compared with the title's: case, accents and a possessive 's don't matter."""
    plain = word.casefold()
    for suffix in ("'s", "\u2019s"):
        plain = plain.removesuffix(suffix)
    return unicodedata.normalize("NFKD", plain).encode("ascii", "ignore").decode() or plain


def title_terms(title: str) -> frozenset[str]:
    """The words and numbers of the title that must stay hidden: all but STOPWORDS."""
    terms = {term(t.text) for t in textplay.tokenize(title) if isinstance(t, textplay.Token)}
    return frozenset(terms - STOPWORDS)


def lead_paragraph(body: Tag) -> Tag | None:
    """The article's first paragraph of text, not counting infoboxes, captions and quotes."""
    for paragraph in body.find_all("p"):
        if paragraph.find_parent(["table", "figure", "blockquote"]) is not None:
            continue
        if paragraph.get_text(strip=True):
            return paragraph
    return None


def lead_names(body: Tag) -> list[str]:
    """The bold text of the article's first paragraph: its full name and other names
    ("**Harry Edward Kane** (born ...", "**Gabon**, officially the **Gabonese Republic**")."""
    paragraph = lead_paragraph(body)
    return [] if paragraph is None else [b.get_text(" ", strip=True) for b in paragraph.find_all("b")]


def lead_sentence_end(text: str, start: int = 0) -> int:
    """Where the first sentence ending at or after start ends in text (past its full stop);
    len(text) if none does. Initials ("J. K.", "U.S.") and ABBREVIATIONS don't end one."""
    for m in SENTENCE_END.finditer(text, start):
        word = re.search(r"(\w+)\W*$", text[: m.start()])
        before = word.group(1) if word else ""
        if m.group() == "." and (len(before) == 1 and before.isalpha() or before.casefold() in ABBREVIATIONS):
            continue
        return m.end()
    return len(text)


def _lead_sentence_nodes(body: Tag) -> dict[int, int]:
    """For each text node (by id) of the lead paragraph that the first sentence reaches,
    how many of its characters it covers. The sentence is the one holding the paragraph's
    first bold text, else its first."""
    paragraph = lead_paragraph(body)
    if paragraph is None:
        return {}
    nodes = [n for n in paragraph.find_all(string=True) if not isinstance(n, Comment)]
    text, starts = "", []
    for node in nodes:
        starts.append(len(text))
        text += str(node)
    bold = paragraph.find("b")
    bold_end = 0
    if bold is not None:
        bold_nodes = {id(n) for n in bold.find_all(string=True)}
        bold_end = max((s + len(n) for s, n in zip(starts, nodes) if id(n) in bold_nodes), default=0)
    end = lead_sentence_end(text, bold_end)
    return {id(n): min(len(n), end - s) for s, n in zip(starts, nodes) if s < end}


def image_key(src: str) -> str:
    """Stable file name stem for a picture's original, from its src as in the downloaded page."""
    return hashlib.sha256(src.encode("utf-8")).hexdigest()[:16]


def load_pictures(folder: Path) -> list[Picture]:
    """Every readable picture in folder; raises ValueError if there is none."""
    pictures: list[Picture] = []
    for path in sorted(folder.iterdir()):
        if path.suffix.lower() not in PICTURE_SUFFIXES or not path.is_file():
            continue
        try:
            with Image.open(path) as image:
                width, height = image.size
        except (UnidentifiedImageError, OSError) as exc:
            logger.warning("skipping unreadable picture %s: %s", path, exc)
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        pictures.append(Picture(path, width, height, digest + path.suffix.lower()))
    if not pictures:
        raise ValueError(f"no pictures in {folder}")
    return pictures


def pick_picture(
    pictures: list[Picture], width: int | None, height: int | None, rng: random.Random
) -> Picture:
    """A random picture of about the given shape, else the closest shape; any if unknown."""
    if not pictures:
        raise ValueError("the picture pool is empty")
    if not width or not height:
        return rng.choice(pictures)
    target = math.log(width / height)

    def off(p: Picture) -> float:
        return abs(math.log(p.width / p.height) - target)

    close = [p for p in pictures if off(p) <= math.log(ASPECT_TOLERANCE)]
    return rng.choice(close) if close else min(pictures, key=off)


def article_title(soup: BeautifulSoup) -> str:
    """The title as shown on the page (h1), else from <title> ("Gabon - Wikipedia")."""
    heading = soup.select_one("h1#firstHeading")
    title = heading.get_text(" ", strip=True) if heading else ""
    if not title and soup.title and soup.title.string:
        title = soup.title.string.rsplit(" - Wikipedia", 1)[0].strip()
    if not title:
        raise ValueError("no article title found (h1#firstHeading or <title>)")
    return " ".join(title.split())


def page_title(html: str) -> str:
    """article_title of an HTML page."""
    return article_title(BeautifulSoup(html, "html.parser"))


def _decompose_all(tags: Iterable[Tag]) -> None:
    for tag in list(tags):
        if not tag.decomposed:  # already gone with a dropped ancestor
            tag.decompose()


def _int_attr(value: object) -> int | None:
    try:
        number = int(str(value))
    except ValueError:
        return None
    return number if number > 0 else None


def _visible_text_nodes(root: Tag) -> list[NavigableString]:
    """Text nodes textplay.map_soup_text would modify."""
    nodes = []
    for node in root.find_all(string=True):
        if isinstance(node, Comment) or node.parent is None or node.parent.name in textplay.SKIP_PARENTS:
            continue
        if node.parent.name == "sup" and node.strip().isdigit():
            continue
        nodes.append(node)
    return nodes


def _title_heading(soup: BeautifulSoup, title: str) -> Tag:
    """The page's title heading, moved out of the page: its markup shows italics (films,
    books, species) as Wikipedia does. Plain title text if the page has none."""
    heading = soup.new_tag("h1", attrs={"class": "firstHeading"})
    original = soup.select_one("h1#firstHeading")
    if original is not None and original.get_text(strip=True):
        for child in list(original.contents):
            heading.append(child.extract())
    else:
        heading.string = title
    return heading


def build_puzzle(
    html: str,
    replace: Callable[[textplay.Token], str],
    pictures: list[Picture],
    rng: random.Random,
    picture_url: str = "pictures/",
) -> Puzzle:
    """Sanitise and scramble a downloaded article page (see the module docstring).

    replace gives each word or number its stand-in; rng picks pictures and the words
    revealed at each of STEP_WORDS; picture_url prefixes picture names.
    """
    soup = BeautifulSoup(html, "html.parser")
    title = article_title(soup)
    body = soup.select_one(BODY_SELECTOR)
    if body is None:
        raise ValueError(f"{BODY_SELECTOR} not found; not a Wikipedia article page?")

    _decompose_all(body.find_all(DROP_TAGS))
    for selector in DROP_SELECTORS:
        _decompose_all(body.select(selector))
    # Comments include the parser report, which names templates and the page.
    for comment in body.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()
    body.insert(0, _title_heading(soup, title))

    hidden = title_terms(title).union(*(title_terms(name) for name in lead_names(body)))
    lead_sentence = _lead_sentence_nodes(body)

    def scramble(text: str) -> str:
        return "".join(p if isinstance(p, str) else replace(p) for p in textplay.tokenize(text))

    used: set[Picture] = set()
    originals: list[str] = []
    for tag in body.find_all(True):
        attrs: dict[str, str | list[str]] = {}
        for name, value in tag.attrs.items():
            if name in KEEP_ATTRS:
                if name == "style" and "url(" in str(value).lower():
                    continue  # background images load from Wikipedia
                attrs[name] = value
            elif name in TEXT_ATTRS:
                attrs[name] = scramble(" ".join(value) if isinstance(value, list) else value)
        if tag.name == "a":
            attrs.update(href=LINK_TARGET, target="_blank", rel="noopener noreferrer")
        elif tag.name == "img":
            if src := str(tag.get("src") or ""):
                attrs["p"] = str(len(originals))
                originals.append(src)
            width, height = _int_attr(attrs.get("width")), _int_attr(attrs.get("height"))
            picture = pick_picture(pictures, width, height, rng)
            used.add(picture)
            # Fill the original's box, cropping what doesn't fit, like ImageOps.fit.
            style = str(attrs.get("style", "")).rstrip("; ")
            attrs.update(
                src=picture_url + picture.name,
                loading="lazy",
                decoding="async",
                style=f"{style}; object-fit: cover" if style else "object-fit: cover",
            )
        tag.attrs = attrs

    tokens: list[tuple[int, str]] = []
    lead_numbers: set[int] = set()  # numbers of the first sentence: hints only
    for node in _visible_text_nodes(body):
        parts = textplay.tokenize(str(node))
        if not any(isinstance(p, textplay.Token) for p in parts):
            continue
        in_heading = node.find_parent(HEADINGS) is not None
        lead_chars = lead_sentence.get(id(node), 0)
        offset = 0
        new: list[NavigableString | Tag] = []
        for part in parts:
            at, offset = offset, offset + len(part if isinstance(part, str) else part.text)
            if isinstance(part, str):
                new.append(NavigableString(part))
                continue
            if term(part.text) in hidden:
                step = NEVER
            elif part.kind == "number" and at < lead_chars:
                step = HINT_ONLY
                lead_numbers.add(len(tokens))
            elif part.kind == "number":
                step = STEP_NUMBERS
            else:
                step = STEP_HEADINGS if in_heading else HINT_ONLY
            wrapper = soup.new_tag(TOKEN_TAG, attrs={"k": str(len(tokens))})
            wrapper.string = replace(part)
            tokens.append((step, part.text))
            new.append(wrapper)
        node.replace_with(*new)
    words = [k for k, (step, _) in enumerate(tokens) if step == HINT_ONLY and k not in lead_numbers]
    # One random order: each step reveals the next words of it, up to its share of them all.
    order = rng.sample(words, round(len(words) * WORD_SHARES[-1]))
    start = 0
    for step, share in zip(STEP_WORDS, WORD_SHARES):
        end = round(len(words) * share)
        for k in order[start:end]:
            tokens[k] = (step, tokens[k][1])
        start = end

    return Puzzle(
        title=title, html=body.decode_contents().strip(), pictures=frozenset(used),
        tokens=tokens, originals=originals,
    )
