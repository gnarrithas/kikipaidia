import {
  useEffect, useLayoutEffect, useMemo, useRef, useState, type MouseEvent, type ReactNode,
} from 'react';
import { createPortal } from 'react-dom';
import '@fontsource/fredoka/700.css';
import { loadPuzzle, type Daily, type Puzzle } from './data/daily';
import {
  HINTS, NEVER, STEP_NAMES, STEPS, canHint, stepsOf, guessCount, isFound, isOver, isShown, loadDailyGame, markOf, newGame, normalizeTitle,
  PICTURE_STEPS, partialTerms, pictureLevel, play, saveDailyGame, type Game, type Move, type Token,
} from './game';
import { type Mark } from './share';
import { DEV_MODE } from './mode';
import {
  isBoolean, isTextSize, isTheme, isWidth, useDark, useSetting, type TextSize, type Theme, type Width,
} from './settings';
import { copyText, shareText, siteUrl } from './share';

/** The guess box: whatever is typed, submitted with Enter. */
function GuessInput({ query, onQuery, onSubmit, disabled }: {
  query: string; onQuery: (q: string) => void; onSubmit: () => void; disabled?: boolean;
}) {
  return (
    <form className="combo" onSubmit={(e) => { e.preventDefault(); onSubmit(); }}>
      <svg className="search-icon" viewBox="0 0 20 20" width="20" height="20" aria-hidden="true">
        <path fill="currentColor" d="M12.2 13.6a7 7 0 111.4-1.4l5.4 5.4-1.4 1.4zM3 8a5 5 0 1010 0A5 5 0 003 8" />
      </svg>
      <input
        disabled={disabled}
        value={query}
        onChange={(e) => onQuery(e.target.value)}
        placeholder="Type the article title, or part of it"
        autoFocus={!disabled}
        spellCheck={false}
        autoComplete="off"
        enterKeyHint="send"
        aria-label="Your guess"
      />
    </form>
  );
}

/**
 * A link in the header's top-right menu (like Wikipedia's "Log in"), opening a panel in the middle of the screen
 * over a dimmed backdrop, rendered into <body>. Clicking the backdrop, the close button or pressing Escape closes it.
 */
function Popover({ id, label, className, children }: {
  id: string; label: string; className?: string; children: ReactNode | ((close: () => void) => ReactNode);
}) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: globalThis.KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);

  return (
    <>
      <button className={className ? `user-link ${className}` : 'user-link'} aria-expanded={open} aria-controls={id} onClick={() => setOpen((o) => !o)}>
        {label}
      </button>
      {open && createPortal(
        <div className="backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) setOpen(false); }}>
          <div id={id} className="popover-panel" role="dialog" aria-modal="true" aria-label={label}>
            <button className="popover-close" aria-label="Close" onClick={() => setOpen(false)} autoFocus>×</button>
            {typeof children === 'function' ? children(() => setOpen(false)) : children}
          </div>
        </div>,
        document.body,
      )}
    </>
  );
}


function Help() {
  return (
    <Popover id="how-to-play" label="How to play">
      <h2>How to play</h2>
      <p>
        This is a real Wikipedia article.
        All words, numbers and images were swapped for a random placeholder.
        Your task is to recover the original article.
      </p>
      <ul>
        <li>The layout is the same: headings, infobox, tables, captions and the wordcount of every sentence.</li>
        <li>
          Every wrong guess, or hint used (<em>Reveal</em> on the left), brings back more of the real information, allowing you to fill the holes.
        </li>
      </ul>
    </Popover>
  );
}

interface Appearance {
  text: TextSize;
  width: Width;
  theme: Theme;
}

function Choices<T extends string>({ name, title, options, value, onChange }: {
  name: string; title: string; options: [T, string][]; value: T; onChange: (v: T) => void;
}) {
  return (
    <fieldset className="appearance-group">
      <legend>{title}</legend>
      {options.map(([v, label]) => (
        <label key={v} className="appearance-option">
          <input type="radio" name={name} value={v} checked={value === v} onChange={() => onChange(v)} />
          {label}
        </label>
      ))}
    </fieldset>
  );
}

/** Wikipedia's Appearance menu: text size, width and color. `name` keeps each copy's radio groups apart. */
function AppearanceOptions({ name, value, onChange }: {
  name: string; value: Appearance; onChange: (v: Partial<Appearance>) => void;
}) {
  return (
    <>
      <Choices
        name={`${name}-text`} title="Text" value={value.text} onChange={(text) => onChange({ text })}
        options={[['small', 'Small'], ['standard', 'Standard'], ['large', 'Large']]}
      />
      <Choices
        name={`${name}-width`} title="Width" value={value.width} onChange={(width) => onChange({ width })}
        options={[['standard', 'Standard'], ['wide', 'Wide']]}
      />
      <Choices
        name={`${name}-color`} title="Color" value={value.theme} onChange={(theme) => onChange({ theme })}
        options={[['auto', 'Automatic'], ['light', 'Light'], ['dark', 'Dark']]}
      />
    </>
  );
}

/**
 * Puts the real text of every shown token and, from the first picture step, the pixelated originals into the
 * article; once the game is over, the unpixelated ones.
 */
function applyReveals(article: HTMLElement, game: Game, tokens: Token[], reveals: string[][]) {
  const over = isOver(game);
  for (const el of article.querySelectorAll<HTMLElement>('g-w:not(.on)')) {
    const k = Number(el.getAttribute('k'));
    if (!isShown(game, tokens, k)) continue;
    el.textContent = tokens[k].text;
    el.classList.add('on');
    // Shown in play (a step or hint), not just at the end: tinted.
    if (isShown({ ...game, feedback: null }, tokens, k)) el.classList.add('revealed');
    if (game.hints.includes(k)) el.classList.add('hinted');
    if (isFound(game, tokens, k)) el.classList.add('found');
    if (over && tokens[k].step === NEVER) el.classList.add('title-word');
  }
  const level = pictureLevel(game);
  if (level === 0) return;
  for (const img of article.querySelectorAll<HTMLImageElement>('img[p]')) {
    const urls = reveals[Number(img.getAttribute('p'))] ?? [];
    const shown = Math.min(level, urls.length);
    const url = urls[shown - 1];
    if (!url || img.dataset.level === String(level)) continue;
    img.src = url;
    img.dataset.level = String(level);
    img.classList.add('on');
    img.classList.toggle('full', shown > PICTURE_STEPS.length);
  }
}

const articleUrl = (title: string) => `https://en.wikipedia.org/wiki/${encodeURIComponent(title.replace(/ /g, '_'))}`;

/** Starts on today's article, as far as it was played before; "Another article" then moves on to earlier days'. */

export default function App({ daily }: { daily: Daily }) {
  const [puzzle, setPuzzle] = useState<Puzzle>(daily.puzzle);
  /** Still on today's puzzle, the only one that is saved and, outside dev mode, shared. */
  const isDaily = puzzle.id === daily.puzzle.id;
  const [game, setGame] = useState<Game>(() => loadDailyGame(daily.day, daily.puzzle.id, daily.puzzle));
  /** Picking a word to reveal with a hint. */
  const [hinting, setHinting] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const articleRef = useRef<HTMLElement>(null);
  const { feedback, wrong } = game;
  const [theme, setTheme] = useSetting<Theme>('theme', 'auto', isTheme);
  useDark(theme);
  const [textSize, setTextSize] = useSetting<TextSize>('text-size', 'standard', isTextSize);
  const [width, setWidth] = useSetting<Width>('width', 'standard', isWidth);
  /** The Appearance menu sits in its own column beside the article (wide screens), else behind a header link. */
  const [pinned, setPinned] = useSetting<boolean>('appearance-pinned', true, isBoolean);
  const appearance: Appearance = { text: textSize, width, theme };
  const setAppearance = (v: Partial<Appearance>) => {
    if (v.text) setTextSize(v.text);
    if (v.width) setWidth(v.width);
    if (v.theme) setTheme(v.theme);
  };
  /** Dev mode: the answer shown mid-game. */
  const [peeking, setPeeking] = useState(false);
  const [copied, setCopied] = useState<'yes' | 'failed' | null>(null);
  const [loadingNext, setLoadingNext] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [query, setQuery] = useState('');

  const done = isOver(game);
  const steps = useMemo(() => stepsOf(puzzle), [puzzle]);
  const attempts = guessCount(game);
  // Other published days' puzzles, for "Another article"; today's is the daily one.
  const others = useMemo(() => [...new Set(daily.ids)].filter((id) => id !== puzzle.id), [daily.ids, puzzle.id]);

  useEffect(() => {
    if (isDaily) saveDailyGame(daily.day, daily.puzzle.id, game);
  }, [isDaily, daily, game]);

  // The article's HTML is set once per puzzle; reveals then edit it in place (React leaves it alone).
  useLayoutEffect(() => {
    if (articleRef.current) applyReveals(articleRef.current, game, puzzle.tokens, puzzle.reveals);
  }, [game, puzzle]);

  const move = (m: Move) => {
    const next = play(game, m, puzzle);
    if (next) {
      setGame(next);
      setCopied(null);
      setNotice(null);
      if (isOver(next)) setHinting(false);
    }
    return next;
  };

  const hintsLeft = HINTS - game.hints.length;

  const onArticleClick = (e: MouseEvent<HTMLElement>) => {
    if (!hinting) return;
    // While picking a word, links don't lead away.
    e.preventDefault();
    const word = (e.target as Element).closest('g-w');
    if (!word) return;
    const k = Number(word.getAttribute('k'));
    if (canHint(game, puzzle.tokens, k)) {
      move({ kind: 'hint', k });
      setHinting(false);
    } else {
      setNotice(puzzle.tokens[k]?.step === NEVER ? "That word can't be revealed. Pick another." : 'That word is already shown.');
    }
  };

  const guess = () => {
    const title = query.trim();
    if (!normalizeTitle(title)) return;
    if (move({ kind: 'guess', title })) {
      setQuery('');
      // The words found show in the title, at the top of the article.
      if (partialTerms(title, puzzle.answer)) window.scrollTo({ top: 0, behavior: 'smooth' });
    } else {
      setNotice(partialTerms(title, puzzle.answer)
        ? `You already found ${title.includes(' ') ? 'those words' : 'that word'} of the title. Try the full title.`
        : `You already guessed “${title}”.`);
    }
  };

  const shareable = isDaily || DEV_MODE;
  const shareLabel = copied === 'yes' ? 'Copied!' : copied === 'failed' ? "Couldn't copy" : 'Share';

  const share = () => {
    const marks = game.moves.map((m) => markOf(m, puzzle.answer)).filter((m): m is Mark => m !== null);
    copyText(shareText(daily.day, marks, game.hints.length, siteUrl())).then(
      () => setCopied('yes'),
      (err) => {
        console.error(err);
        setCopied('failed');
      },
    );
  };

  const next = () => {
    const id = others[Math.floor(Math.random() * others.length)];
    setLoadingNext(true);
    setLoadError(false);
    loadPuzzle(id).then(
      (p) => {
        setPuzzle(p);
        setGame(newGame());
        setCopied(null);
        setPeeking(false);
        setHinting(false);
        setNotice(null);
        setQuery('');
        setLoadingNext(false);
        window.scrollTo({ top: 0 });
      },
      (err) => {
        console.error(err);
        setLoadError(true);
        setLoadingNext(false);
      },
    );
  };

  const answerLink = (
    <a href={articleUrl(puzzle.answer)} target="_blank" rel="noopener noreferrer">{puzzle.answer}</a>
  );

  const status = loadError ? (
    <span className="bad">Couldn't load another article. Please try again.</span>
  ) : notice ? (
    <span className="bad">{notice}</span>
  ) : hinting ? (
    <>Click a word to reveal it.</>
  ) : feedback?.kind === 'correct' ? (
    <span className="good">
      Correct! This is {answerLink}{attempts > 1 && ` — found in ${attempts} guesses`}.
    </span>
  ) : feedback?.kind === 'revealed' ? (
    <span>The answer is {answerLink}.</span>
  ) : feedback?.kind === 'partial' ? (
    <span className="partial">
      Yes! <strong>{feedback.title}</strong> is part of the title — now shown at the top of the article.
      {' '}Can you finish it? Type the full title.
    </span>
  ) : feedback?.kind === 'wrong' ? (
    <span className="bad">
      It is not <strong>{feedback.title}</strong>.
      {game.step > 0 && <> Now revealed: {STEP_NAMES[game.step - 1].toLowerCase()}.</>}
    </span>
  ) : feedback?.kind === 'skipped' ? (
    <>Now revealed: {STEP_NAMES[game.step - 1].toLowerCase()}.</>
  ) : (
    <>Recover the original article. Type its title, or any part of it, and press Enter.</>
  );

  return (
    <div className={`wiki text-${textSize} width-${width}`}>
      {/* Laid out like Wikipedia: logo, search (the guess, under the game's status) and user links across the top, contents on the left. */}
      <div className="top">
      <header className="site-header">
        <div className="logo">
          <span className="wordmark">
            <span className="brand">K<span className="brand-mid">ikipaidi</span>A</span>
            <span className="tagline">The Free Puzzle</span>
          </span>
        </div>
        <div className="search">
          <div className="site-notice" role="status">
            <span>{status}</span>
          </div>
          <div className="search-row">
            <GuessInput query={query} onQuery={(q) => { setQuery(q); setNotice(null); }} onSubmit={guess} disabled={done} />
            {done && (
              <span className="actions">
                {shareable && <button className="primary" onClick={share} autoFocus>{shareLabel}</button>}
                {others.length > 0 && (
                  <button className={shareable ? undefined : 'primary'} onClick={next} disabled={loadingNext} autoFocus={!shareable}>
                    {loadingNext ? 'Loading...' : 'Next'}
                  </button>
                )}
              </span>
            )}
          </div>
        </div>
        <nav className="user-links" aria-label="Game menu">
          {DEV_MODE && !done && (
            <button className="user-link dev" onClick={() => setPeeking((p) => !p)} aria-pressed={peeking}>
              {peeking ? puzzle.answer : 'Reveal'}
            </button>
          )}
          {DEV_MODE && !done && <button className="user-link dev" onClick={share}>{shareLabel}</button>}
          <Help />
          <Popover id="appearance" label="Appearance" className={pinned ? 'appearance-link pinned' : 'appearance-link'}>
            {(close) => (
              <>
                <div className="appearance-header">
                  <h2>Appearance</h2>
                  <button className="appearance-pin" onClick={() => { setPinned(true); close(); }}>move to sidebar</button>
                </div>
                <AppearanceOptions name="popover" value={appearance} onChange={setAppearance} />
              </>
            )}
          </Popover>
        </nav>
      </header>
      </div>

      <div className="columns">
        <aside className="toc" aria-label="Game">
          <div className="toc-title">Revealed:</div>
          <ul
            className="toc-steps"
            aria-label={`Revealed ${done ? steps.length : steps.filter((s) => s <= game.step).length} of ${steps.length}`}
          >
            {steps.map((s) => {
              const name = STEP_NAMES[s - 1];
              const on = done || s <= game.step;
              return (
                <li key={name} className={on ? 'on' : undefined}>
                  <input type="checkbox" checked={on} readOnly tabIndex={-1} />
                  <span>{name}</span>
                </li>
              );
            })}
          </ul>
          {!done && (
            <ul className="toc-actions">
              <li>
                <button className="toc-link" onClick={() => move({ kind: 'skip' })} disabled={game.step >= STEPS}>
                  Reveal more
                </button>
              </li>
              <li>
                <button
                  className={hinting ? 'toc-link pressed' : 'toc-link'}
                  onClick={() => { setHinting((h) => !h); setNotice(null); }}
                  disabled={hintsLeft === 0}
                  aria-pressed={hinting}
                  title="Reveal one word of your choice"
                >
                  Reveal a word ({hintsLeft} left)
                </button>
              </li>
              <li>
                <button className="toc-link" onClick={() => move({ kind: 'give-up' })}>Give up</button>
              </li>
            </ul>
          )}
          {wrong.length > 0 && (
            <>
              <div className="toc-subtitle">Wrong guesses</div>
              <ul className="wrong-list">
                {wrong.map((t) => <li key={t}>{t}</li>)}
              </ul>
            </>
          )}
        </aside>

        <main className="page">
          {/* Built and sanitised by scripts/build_puzzles.py: no scripts, links all lead to wikipedia.org. */}
          <article
            key={puzzle.id}
            ref={articleRef}
            className={hinting ? 'article mw-parser-output hinting' : 'article mw-parser-output'}
            onClick={onArticleClick}
            dangerouslySetInnerHTML={{ __html: puzzle.html }}
          />
        </main>

        {pinned && (
          <aside className="appearance" aria-label="Appearance">
            <div className="appearance-header">
              <span className="appearance-title">Appearance</span>
              <button className="appearance-pin" onClick={() => setPinned(false)}>hide</button>
            </div>
            <AppearanceOptions name="sidebar" value={appearance} onChange={setAppearance} />
          </aside>
        )}
      </div>
    </div>
  );
}
