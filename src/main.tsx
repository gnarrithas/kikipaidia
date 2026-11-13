import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './App.css';
import './article.css';
import App from './App';
import { loadDaily } from './data/daily';

const root = createRoot(document.getElementById('root')!);

loadDaily().then(
  (daily) =>
    root.render(
      <StrictMode>
        <App daily={daily} />
      </StrictMode>,
    ),
  (err) => {
    console.error(err);
    root.render(<p className="load-error">Couldn't load today's article. Please reload the page.</p>);
  },
);
