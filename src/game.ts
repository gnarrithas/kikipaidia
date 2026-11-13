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
  /** The guess is part of the title: terms newly found. */
  | { kind: 'partial'; title: string; terms: string[] }
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
  /** Title terms (see term) found by partial guesses, in order. */
  found: string[];
}

export const newGame = (): Game => ({ moves: [], feedback: null, wrong: [], step: 0, hints: [], found: [] });

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

// puzzle.py's STOPWORDS: title words the article shows anyway, so they don't make a guess partial.
const STOPWORDS = new Set(`a an the and or nor but of in on at to for from by with without over under into onto upon
about above across after against along among around as before behind below beneath beside
between beyond during except inside near off out outside since than through throughout till
toward towards until up via within is are was were be it its this that de la le les du des
del der den die das von van y et el`.split(/\s+/));

/** A word as compared with the title's, like puzzle.py's term: case, accents and a possessive 's don't matter. */
export const term = (word: string) =>
  word.normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase().replace(/['’]s$/u, '');

/** The terms of a text, stopwords left out. */
const termsOf = (text: string) =>
  text.split(/[^\p{L}\p{N}'’]+/u).map((w) => term(w.replace(/^['’]+|['’]+$/gu, '')))
    .filter((t) => t && !STOPWORDS.has(t));

/**
 * The terms of a guess that is part of the answer (every word of it, stopwords aside, is one of the title's);
 * null for any other guess.
 */
export function partialTerms(guess: string, answer: string): string[] | null {
  const terms = [...new Set(termsOf(guess))];
  const title = new Set(termsOf(answer));
  return terms.length > 0 && terms.every((t) => title.has(t)) ? terms : null;
}

/** Whether token k shows its real text: its step is reached, a hint revealed it, or the game is over. */
export const isShown = (game: Game, tokens: Token[], k: number) =>
  isOver(game) || game.hints.includes(k) || (tokens[k] !== undefined && (
    (tokens[k].step > HINT_ONLY && tokens[k].step <= game.step) || isFound(game, tokens, k)));

/** Whether token k is a title word a partial guess found. */
export const isFound = (game: Game, tokens: Token[], k: number) =>
  tokens[k] !== undefined && tokens[k].step === NEVER && game.found.includes(term(tokens[k].text));

/** Whether a hint may reveal token k now (title words never). */
export const canHint = (game: Game, tokens: Token[], k: number) =>
  !isOver(game) && game.hints.length < HINTS && tokens[k] !== undefined && tokens[k].step !== NEVER && !isShown(game, tokens, k);

const nextStep = (game: Game, rules: Rules) => stepsOf(rules).find((s) => s > game.step) ?? STEPS;

/**
 * The game after one more move; null for a move that can't be made (game over, repeated guess, a part of the
 * title already found, no hint left...). A guess that is part of the title costs nothing: it shows those words.
 */
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
      const partial = partialTerms(move.title, rules.answer);
      if (partial) {
        const terms = partial.filter((t) => !game.found.includes(t));
        if (!terms.length) return null;
        return { ...game, moves, found: [...game.found, ...terms], feedback: { kind: 'partial', title: move.title, terms } };
      }
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
      if (normalizeTitle(move.title) === normalizeTitle(answer)) return MARKS.right;
      return partialTerms(move.title, answer) ? MARKS.partial : MARKS.wrong;
  }
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
