#!/usr/bin/env python3
"""Load a PNG as an RGBA matrix, run `process` on it, and save the result.

Edit `process` to experiment with image processing.
"""

import argparse
import json
import logging
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt
from PIL import Image, ImageFilter, UnidentifiedImageError

logger = logging.getLogger(__name__)

type RGBA = npt.NDArray[np.uint8]
"""Image matrix of shape (height, width, 4), channels R, G, B, A in 0..255."""


def black_and_white(rgba: RGBA, threshold: int = 15) -> RGBA:
    """Set RGB to white where the mean of R, G, B is >= threshold, else black; keep alpha."""
    out = rgba.copy()
    # Sum in a wider type: uint8 + uint8 + uint8 overflows past 255.
    average = rgba[..., :3].sum(axis=-1, dtype=np.uint16) / 3
    out[..., :3] = np.where(average >= threshold, 255, 0)[..., None]
    return out


def process(rgba: RGBA, window_size: int = 2) -> RGBA:
    """Pixelate: replace each window_size x window_size block with its average colour.

    Blocks at the right/bottom edges may be smaller; they are averaged over the
    pixels they actually contain.
    """
    if window_size < 1:
        raise ValueError(f"window_size must be >= 1, got {window_size}")
    height, width = rgba.shape[:2]
    if height == 0 or width == 0:
        return rgba.copy()
    row_starts = np.arange(0, height, window_size)
    col_starts = np.arange(0, width, window_size)
    # Sum every block (edge blocks included): first along rows, then along columns.
    sums = np.add.reduceat(
        np.add.reduceat(rgba.astype(np.uint64), row_starts, axis=0), col_starts, axis=1
    )
    # Pixel count per block; only the last row/column of blocks can be smaller.
    rows_per_block = np.diff(row_starts, append=height)
    cols_per_block = np.diff(col_starts, append=width)
    counts = np.outer(rows_per_block, cols_per_block)[..., None]
    means = (sums / counts).astype(np.uint8)  # truncates, like the original loop
    # Stretch each block's mean back over its pixels.
    return np.repeat(np.repeat(means, rows_per_block, axis=0), cols_per_block, axis=1)


@dataclass(frozen=True)
class Box:
    """Pixel rectangle: left/top inclusive, right/bottom exclusive."""

    left: int
    top: int
    right: int
    bottom: int


def load_boxes(path: Path) -> list[Box]:
    """Read boxes written by capture.py (`<image>.images.json`)."""
    data = json.loads(path.read_text())
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a JSON list of boxes")
    boxes: list[Box] = []
    for i, item in enumerate(data):
        try:
            box = Box(**{k: int(item[k]) for k in ("left", "top", "right", "bottom")})
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{path}: box {i} is malformed: {item!r}") from exc
        if box.right <= box.left or box.bottom <= box.top:
            logger.warning("%s: skipping empty box %d: %s", path, i, box)
            continue
        boxes.append(box)
    return boxes


def blur_regions(rgba: RGBA, boxes: list[Box], radius: float = 8.0) -> RGBA:
    """Gaussian-blur each box; boxes are clipped to the image, pixels outside are untouched."""
    out = rgba.copy()
    height, width = rgba.shape[:2]
    for box in boxes:
        left, top = max(0, box.left), max(0, box.top)
        right, bottom = min(width, box.right), min(height, box.bottom)
        if right <= left or bottom <= top:
            continue
        region = Image.fromarray(np.ascontiguousarray(rgba[top:bottom, left:right]))
        out[top:bottom, left:right] = np.asarray(region.filter(ImageFilter.GaussianBlur(radius)))
    return out


def pixelate_regions(rgba: RGBA, boxes: list[Box], window_size: int = 8) -> RGBA:
    """Pixelate each box (see `process`); boxes are clipped to the image, pixels outside are untouched."""
    out = rgba.copy()
    height, width = rgba.shape[:2]
    for box in boxes:
        left, top = max(0, box.left), max(0, box.top)
        right, bottom = min(width, box.right), min(height, box.bottom)
        if right <= left or bottom <= top:
            continue
        out[top:bottom, left:right] = process(rgba[top:bottom, left:right], window_size)
    return out


def pixel_blocks(image: Image.Image, window: int, display_width: int | None = None) -> Image.Image:
    """The image as one pixel per window x window block (its average colour), as `process`
    pixelates it but without stretching the blocks back: show it scaled up with CSS
    `image-rendering: pixelated`. window counts pixels as displayed at display_width
    (default: the image's own width)."""
    if window < 1:
        raise ValueError(f"window must be >= 1, got {window}")
    scale = image.width / display_width if display_width else 1.0
    block = max(window * scale, 1.0)
    size = (max(1, math.ceil(image.width / block)), max(1, math.ceil(image.height / block)))
    return image.convert("RGBA").resize(size, Image.Resampling.BOX)


def load_rgba(path: Path) -> RGBA:
    """Read an image file and return it as an RGBA uint8 matrix."""
    with Image.open(path) as image:
        logger.info("loaded %s: %dx%d, mode %s", path, image.width, image.height, image.mode)
        return np.asarray(image.convert("RGBA"), dtype=np.uint8).copy()


def save_rgba(rgba: RGBA, path: Path) -> None:
    """Validate an RGBA matrix and write it as a PNG."""
    if rgba.ndim != 3 or rgba.shape[2] != 4:
        raise ValueError(f"expected shape (height, width, 4), got {rgba.shape}")
    if rgba.shape[0] == 0 or rgba.shape[1] == 0:
        raise ValueError(f"image has no pixels: shape {rgba.shape}")
    if rgba.dtype != np.uint8:
        logger.warning("result dtype is %s; clipping to 0..255 and casting to uint8", rgba.dtype)
        rgba = np.clip(rgba, 0, 255).astype(np.uint8)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba).save(path, format="PNG")
    logger.info("saved %s", path)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="input PNG")
    parser.add_argument(
        "-o", "--output", type=Path, help="output PNG (default: <input>_out.png)"
    )
    parser.add_argument("--show", action="store_true", help="open the result in a viewer")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    input_path: Path = args.input
    output_path: Path = args.output or input_path.with_name(f"{input_path.stem}_out.png")
    if output_path.resolve() == input_path.resolve():
        logger.error("refusing to overwrite the input file %s", input_path)
        return 2

    try:
        rgba = load_rgba(input_path)
    except FileNotFoundError:
        logger.error("no such file: %s", input_path)
        return 1
    except (UnidentifiedImageError, OSError) as exc:
        logger.error("cannot read %s as an image: %s", input_path, exc)
        return 1

    result = process(rgba)

    try:
        save_rgba(result, output_path)
    except (ValueError, OSError) as exc:
        logger.error("cannot save result: %s", exc)
        return 1

    if args.show:
        with Image.open(output_path) as saved:
            saved.show()
    return 0


if __name__ == "__main__":
    sys.exit(main())
