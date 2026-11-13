"""Text modifications for article HTML, applied before rendering with wikipage.render_html.

    html = Path("gabon.html").read_text()
    rng = random.Random(1)
    html = map_text_nodes(html, lambda t: replace_numbers(replace_words(t, words, rng), rng))
"""

import random
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from bs4 import BeautifulSoup, NavigableString, Tag
from bs4.element import Comment

CONTENT_SELECTOR = "main#content"
SKIP_PARENTS = {"script", "style", "noscript"}

# Letter = word character that is neither a digit nor "_" (works for any script/accents).
_L = r"[^\W\d_]"
# Whole words only: tokens touching letters/digits on either side (13th, 1990s) aren't words.
WORD_RE = re.compile(rf"(?<![^\W_]){_L}+(?:['’]{_L}+)*(?![^\W_])")
# 1,234,567.89 or 1234.5 or 42, with an optional ordinal suffix. Numbers glued to a letter,
# digit, "." or "," on the left (km2, B52, the 5 in 1.5) are not matched.
NUMBER_RE = re.compile(
    rf"(?<!{_L})(?<![\d.,])(\d{{1,3}}(?:,\d{{3}})+(?:\.\d+)?|\d+(?:\.\d+)?)((?:st|nd|rd|th)(?!{_L}))?"
)
YEAR_MIN, YEAR_MAX = 1000, 4000


def load_words(path: Path) -> list[str]:
    """Read one word per line; blank lines and surrounding whitespace are ignored."""
    words = [w.strip() for w in path.read_text(encoding="utf-8").splitlines() if w.strip()]
    if not words:
        raise ValueError(f"{path} contains no words")
    return words


def map_text_nodes(
    html: str, fn: Callable[[str], str], selector: str = CONTENT_SELECTOR
) -> str:
    """Apply fn to every visible text node inside `selector`; return the new HTML.

    Skips script/style text, comments, and bare-digit <sup> nodes (exponents as in km²).
    """
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one(selector)
    if root is None:
        raise ValueError(f"{selector} not found in HTML")
    map_soup_text(root, fn)
    return str(soup)


def map_soup_text(root: Tag, fn: Callable[[str], str]) -> None:
    """In place: apply fn to every visible text node under root (see map_text_nodes)."""
    for node in list(root.find_all(string=True)):
        if isinstance(node, Comment) or node.parent is None or node.parent.name in SKIP_PARENTS:
            continue
        if node.parent.name == "sup" and node.strip().isdigit():
            continue
        new = fn(str(node))
        if new != node:
            node.replace_with(NavigableString(new))


def _restyle(original: str, word: str) -> str:
    """Copy the original's capitalisation (ALL CAPS or Capitalised) onto word."""
    if len(original) > 1 and original.isupper():
        return word.upper()
    if original[0].isupper():
        return word[:1].upper() + word[1:]
    return word


def replace_words(text: str, words: list[str], rng: random.Random) -> str:
    """Replace each word by a random one from `words`, keeping capitalisation.

    Single letters (compass points in 1°S, V·T·E links, initials) are kept, except a/I.
    """
    if not words:
        raise ValueError("word list is empty")

    def sub(m: re.Match[str]) -> str:
        w = m.group(0)
        if len(w) == 1 and w not in "aAI":
            return w
        return _restyle(w, rng.choice(words))

    return WORD_RE.sub(sub, text)


@dataclass(frozen=True)
class Token:
    """A word or number of a text, as tokenize finds them."""

    kind: Literal["word", "number"]
    text: str


_TOKEN_RE = re.compile(rf"(?P<word>{WORD_RE.pattern})|(?P<number>{NUMBER_RE.pattern})")


def tokenize(text: str) -> list[str | Token]:
    """Split text into the words and numbers replace_words/replace_numbers would replace,
    and the plain text between them (single letters other than a/I stay plain)."""
    parts: list[str | Token] = []
    last = 0
    for m in _TOKEN_RE.finditer(text):
        word = m.group("word")
        if word is not None and len(word) == 1 and word not in "aAI":
            continue
        if m.start() > last:
            parts.append(text[last:m.start()])
        parts.append(Token("word" if word is not None else "number", m.group(0)))
        last = m.end()
    if last < len(text):
        parts.append(text[last:])
    return parts


def replace_token(token: Token, words: list[str], rng: random.Random) -> str:
    """A random stand-in for one token, as replace_words/replace_numbers make them."""
    if token.kind == "number":
        return replace_numbers(token.text, rng)
    if not words:
        raise ValueError("word list is empty")
    return _restyle(token.text, rng.choice(words))


def _ordinal(n: int) -> str:
    if 11 <= n % 100 <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def replace_numbers(text: str, rng: random.Random) -> str:
    """Replace years by random years; scale other numbers by a random factor in 10^-2..10^2.

    A year is a 4-digit number in 1000..4000; it becomes a random year in that range.
    Other numbers keep their decimals, thousands separators and ordinal suffix (13th);
    a non-zero number never becomes 0.
    """

    def sub(m: re.Match[str]) -> str:
        num, suffix = m.group(1), m.group(2)
        if len(num) == 4 and num.isdigit() and YEAR_MIN <= int(num) <= YEAR_MAX:
            value: float = rng.randint(YEAR_MIN, YEAR_MAX)
            out = str(int(value))
        else:
            original = float(num.replace(",", ""))
            decimals = len(num.split(".")[1]) if "." in num else 0
            value = round(original * 10 ** rng.uniform(-2, 2), decimals)
            if value == 0 and original != 0:
                value = 10.0**-decimals
            out = f"{value:,.{decimals}f}" if "," in num else f"{value:.{decimals}f}"
        return out + (_ordinal(round(value)) if suffix else "")

    return NUMBER_RE.sub(sub, text)
