import { MARKS, type Mark } from './share';

/**
 * Reveal steps, as scripts/build_puzzles.py tags each word (puzzle.py's STEP_*): every wrong guess
 * or skip shows the real text of one more step. NEVER words (the title's) only show once the game ends;
 * HINT_ONLY ones when the player spends a hint on them.
 */
export const NEVER = -1;
export const HINT_ONLY = 0;
/** What each step shows, in order (puzzle.py's module docstring). */
export const STEP_NAMES = [
  'Numbers and years', 'Pixelated images', '10% of words', 'Smoother images', '25% of words', 'Section headings',
  'Even smoother images', '50% of words',
] as const;
export const STEPS = STEP_NAMES.length;
/** The steps showing the original pictures, ever less pixelated (puzzle.py's PICTURE_STEPS). */
export const PICTURE_STEPS: readonly number[] = [2, 4, 7];

/**
 * Which of each picture's reveals is shown: 0 for none, up to PICTURE_STEPS.length; once the game is
 * over, one more, the unpixelated original (pictures without one keep their last pixelated reveal).
 */
export const pictureLevel = (game: Game) =>
  isOver(game) ? PICTURE_STEPS.length + 1 : PICTURE_STEPS.filter((s) => s <= game.step).length;
export const HINTS = 5;

/** A word or number of the article: its real text, and the step that shows it. */
export interface Token {
  step: number;
  text: string;
}

/** What play needs to know about the puzzle. */
export interface Rules {
  answer: string;
  tokens: Token[];
  /** Each picture's originals, one per picture step, then unpixelated if there is one; [] if none. */
  reveals: string[][];
}

/** Whether the article has any picture to reveal: without one, the picture steps are left out. */
export const hasPictures = (rules: Rules) => rules.reveals.some((urls) => urls.length > 0);

/** The steps the puzzle goes through (1 to STEPS), picture steps only if it has pictures. */
export const stepsOf = (rules: Rules) =>
  STEP_NAMES.map((_, i) => i + 1).filter((s) => hasPictures(rules) || !PICTURE_STEPS.includes(s));

/** A player's move: what gets saved, replayed and shared. */
export type Move =
  | { kind: 'guess'; title: string }
  | { kind: 'skip' }
  | { kind: 'hint'; k: number }
  | { kind: 'give-up' };

export type Feedback =
  | { kind: 'wrong'; title: string }
  | { kind: 'skipped' }
  | { kind: 'correct' }
  | { kind: 'revealed' }
  | null;

export interface Game {
  moves: Move[];
  feedback: Feedback;
  /** Wrong titles guessed, in order. */
  wrong: string[];
  /** Reveal steps shown, 0 to STEPS. */
  step: number;
  /** Tokens revealed by hints, in order. */
  hints: number[];
}

export const newGame = (): Game => ({ moves: [], feedback: null, wrong: [], step: 0, hints: [] });

export const isOver = (game: Game) => game.feedback?.kind === 'correct' || game.feedback?.kind === 'revealed';

export const guessCount = (game: Game) => game.moves.filter((m) => m.kind === 'guess').length;

/** Case, accents, punctuation and spacing don't matter. */
export const normalizeTitle = (title: string) =>
  title
    .normalize('NFKD')
    .replace(/\p{M}/gu, '')
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, ' ')
    .trim();

/** Whether token k shows its real text: its step is reached, a hint revealed it, or the game is over. */
export const isShown = (game: Game, tokens: Token[], k: number) =>
  isOver(game) || game.hints.includes(k) || (tokens[k] !== undefined && tokens[k].step > HINT_ONLY && tokens[k].step <= game.step);

/** Whether a hint may reveal token k now (title words never). */
export const canHint = (game: Game, tokens: Token[], k: number) =>
  !isOver(game) && game.hints.length < HINTS && tokens[k] !== undefined && tokens[k].step !== NEVER && !isShown(game, tokens, k);

const nextStep = (game: Game, rules: Rules) => stepsOf(rules).find((s) => s > game.step) ?? STEPS;

/** The game after one more move; null for a move that can't be made (game over, repeated guess, no hint left...). */
export function play(game: Game, move: Move, rules: Rules): Game | null {
  if (isOver(game)) return null;
  const moves = [...game.moves, move];
  switch (move.kind) {
    case 'give-up':
      return { ...game, moves, feedback: { kind: 'revealed' } };
    case 'skip':
      if (game.step >= STEPS) return null;
      return { ...game, moves, step: nextStep(game, rules), feedback: { kind: 'skipped' } };
    case 'hint':
      if (!canHint(game, rules.tokens, move.k)) return null;
      return { ...game, moves, hints: [...game.hints, move.k] };
    case 'guess': {
      const guess = normalizeTitle(move.title);
      if (!guess || game.wrong.some((t) => normalizeTitle(t) === guess)) return null;
      if (guess === normalizeTitle(rules.answer)) return { ...game, moves, feedback: { kind: 'correct' } };
      return {
        ...game, moves, step: nextStep(game, rules), wrong: [...game.wrong, move.title],
        feedback: { kind: 'wrong', title: move.title },
      };
    }
  }
}

/** One emoji per move, for sharing; hints have none (the share counts them). */
export function markOf(move: Move, answer: string): Mark | null {
  switch (move.kind) {
    case 'give-up':
      return MARKS.gaveUp;
    case 'skip':
      return MARKS.skipped;
    case 'hint':
      return null;
    case 'guess':
      return normalizeTitle(move.title) === normalizeTitle(answer) ? MARKS.right : MARKS.wrong;
  }
}

/** Titles containing every word typed, those starting with the query first. */
export function searchTitles(query: string, titles: string[], limit = 50): string[] {
  const q = normalizeTitle(query);
  if (!q) return [];
  const words = q.split(' ');
  const scored = titles
    .map((title) => ({ title, norm: normalizeTitle(title) }))
    .filter(({ norm }) => words.every((w) => norm.includes(w)));
  scored.sort((a, b) => Number(!a.norm.startsWith(q)) - Number(!b.norm.startsWith(q)) || a.norm.localeCompare(b.norm));
  return scored.slice(0, limit).map(({ title }) => title);
}

// One saved game at a time: today's. Another day's or another puzzle's (a new deploy) is ignored.
const STORAGE_KEY = 'kikipaidia:daily';

interface Saved {
  day: number;
  puzzle: string;
  moves: Move[];
}

const isMove = (m: unknown): m is Move => {
  if (typeof m !== 'object' || m === null) return false;
  const { kind, title, k } = m as { kind?: unknown; title?: unknown; k?: unknown };
  return kind === 'give-up' || kind === 'skip' || (kind === 'guess' && typeof title === 'string')
    || (kind === 'hint' && Number.isInteger(k));
};

/** Today's game as saved, replayed from its moves; a new game when there is none or it can't be read. */
export function loadDailyGame(day: number, puzzleId: string, rules: Rules): Game {
  let game = newGame();
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return game;
    const saved = JSON.parse(raw) as Partial<Saved>;
    if (saved.day !== day || saved.puzzle !== puzzleId || !Array.isArray(saved.moves)) return game;
    for (const move of saved.moves) {
      // Stops at the first move that no longer fits, keeping the game up to there.
      const next = isMove(move) ? play(game, move, rules) : null;
      if (!next) break;
      game = next;
    }
  } catch (err) {
    console.warn('Ignoring the saved game', err);
  }
  return game;
}

export function saveDailyGame(day: number, puzzleId: string, game: Game) {
  const saved: Saved = { day, puzzle: puzzleId, moves: game.moves };
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(saved));
  } catch (err) {
    // Private browsing or a full quota: the game goes on, unsaved.
    console.warn('Could not save the game', err);
  }
}
