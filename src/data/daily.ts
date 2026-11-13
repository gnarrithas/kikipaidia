import type { Token } from '../game';

const MS_IN_DAY = 86400000;
// Day 0 of the daily sequence: 12 November 2026 (months count from 0). Must
// match FIRST_DAY in scripts/build_puzzles.py.
const FIRST_DAY_UTC = Date.UTC(2026, 10, 12);
/** Where the deploy writes scripts/build_puzzles.py's output (SERVED_AT there). */
const GAME_URL = `${import.meta.env.BASE_URL}game/`;
const ID = /^[0-9a-f]{16}$/;

/**
 * Today's day number, from the earlier of the local and UTC dates: the deploy
 * publishes a day only once it has started in UTC. Clamped to the published days.
 */
export function dayIndex(published: number, now = new Date()): number {
  const local = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  const utc = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  const day = Math.round((Math.min(local, utc) - FIRST_DAY_UTC) / MS_IN_DAY);
  return Math.min(Math.max(0, day), published - 1);
}

async function fetchText(name: string): Promise<string> {
  const response = await fetch(GAME_URL + name, { cache: 'no-cache' });
  if (!response.ok) throw new Error(`GET ${name} failed: ${response.status}`);
  return response.text();
}

const lines = (text: string) => text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);

export interface Puzzle {
  id: string;
  /** The article's title. */
  answer: string;
  /** The article body, sanitised and scrambled by scripts/build_puzzles.py. */
  html: string;
  /** The real text of each <g-w k>, by k. */
  tokens: Token[];
  /**
   * URLs of each <img p>'s original, ever less pixelated (one per PICTURE_STEPS), then unpixelated (if
   * downloaded), by p; [] if there is none.
   */
  reveals: string[][];
}

export interface Daily {
  /** Puzzle ids of day 0 through today (UTC): the deploy publishes no later day. */
  ids: string[];
  /** Today's index into ids. */
  day: number;
  puzzle: Puzzle;
}

/** Base64 of UTF-8 text, as the build script writes the answer. */
const decodeBase64 = (text: string) =>
  new TextDecoder().decode(Uint8Array.from(atob(text), (c) => c.charCodeAt(0)));

const isToken = (t: unknown): t is [number, string] =>
  Array.isArray(t) && t.length === 2 && Number.isInteger(t[0]) && typeof t[1] === 'string';

export async function loadPuzzle(id: string): Promise<Puzzle> {
  const data = JSON.parse(await fetchText(`puzzles/${id}.json`)) as {
    answer?: unknown; html?: unknown; tokens?: unknown; reveals?: unknown;
  };
  const tokens: unknown = typeof data.tokens === 'string' ? JSON.parse(decodeBase64(data.tokens)) : null;
  if (
    typeof data.answer !== 'string' || typeof data.html !== 'string'
    || !Array.isArray(tokens) || !tokens.every(isToken)
    || !Array.isArray(data.reveals)
    || !data.reveals.every((r) => Array.isArray(r) && r.every((url) => typeof url === 'string'))
  ) {
    throw new Error(`puzzles/${id}.json is not a puzzle`);
  }
  return {
    id,
    answer: decodeBase64(data.answer),
    html: data.html,
    tokens: tokens.map(([step, text]) => ({ step, text })),
    // Served next to the game: relative to the site root, like the pool pictures in the HTML.
    reveals: (data.reveals as string[][]).map((urls) => urls.map((url) => `${import.meta.env.BASE_URL}${url}`)),
  };
}

/** The published days and today's puzzle. */
export async function loadDaily(): Promise<Daily> {
  const ids = lines(await fetchText('daily.txt'));
  // Also rejects an HTML page served in place of a missing file.
  if (ids.length === 0 || !ids.every((id) => ID.test(id))) throw new Error('daily.txt is empty or malformed');
  const day = dayIndex(ids.length);
  return { ids, day, puzzle: await loadPuzzle(ids[day]) };
}
