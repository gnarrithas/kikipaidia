# Kikipaidia

Guess the Wikipedia article. Every word and picture of a real article is swapped
for a random one, and every number for another of about the same size; the
layout stays. A new article every day.

## Development

Requires Node.js 22 and [uv](https://docs.astral.sh/uv/).

```sh
npm install         # install dependencies
npm run puzzles     # build every puzzle into public/game/ (dev seed)
npm run dev         # dev server on http://localhost:3300
npm run build       # type-check and build into dist/
uv run python -m unittest discover -s scripts   # Python tests
```

## Content

- `data/articles/<slug>.html`: article pages, from
  `scripts/download_from_wikipedia.py <list.txt> -o data/articles` (one title
  or URL per line).
  It also saves each article's pictures pixelated (16 px blocks) in
  `data/articles/<slug>.images/`; the build makes 64 and 32 px versions from
  them, and the game reveals the three in turn. Unpixelated copies (WebP, at
  most twice the displayed size) sit next to them and show once the game is
  over.
- `data/pictures/`: the replacement picture pool. Fill it
  with `scripts/download_from_openverse.py` (200 CC0 / public domain photos
  from Openverse, no API key; credits in `openverse.json`).
- `words.txt`: the replacement words, one per line.

`scripts/build_puzzles.py` turns them into the published puzzles: only the
article body is kept, every link leads to https://www.wikipedia.org/, scripts,
ids and metadata go, and text, alt/title attributes and pictures are replaced
at random (see `puzzle.py`).

## Deployment

`.github/workflows/pages.yml` deploys to GitHub Pages on every push to `main`
and daily just after midnight UTC. It needs the repository secret
`SOLUTIONS_SHUFFLE_SEED`, which fixes the order of the articles: keep it
secret, and don't change it once live (that reorders every day). Pages must be
set to deploy from GitHub Actions.

## Playground

`playground.ipynb` (`uv run jupyter lab`) is for image and text experiments:
pixelation, rendering modified articles to images (`wikipage.py`, `textplay.py`).
