import { useEffect, useState, type ReactNode } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { api } from '../api';

const Icon = ({ children }: { children: ReactNode }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{children}</svg>
);

const NAV = [
  { to: '/', label: 'Latest issue', end: true, icon: <Icon><path d="M4 18a8 8 0 0 1 16 0" /><path d="M8 18a4 4 0 0 1 8 0" /><circle cx="12" cy="18" r="1" /><path d="M2 12a14 14 0 0 1 20 0" /></Icon> },
  { to: '/findings', label: 'Findings', icon: <Icon><path d="M4 6h16" /><path d="M4 12h16" /><path d="M4 18h10" /></Icon> },
  { to: '/runs', label: 'Runs', icon: <Icon><path d="M12 7v5l3 2" /><circle cx="12" cy="12" r="9" /></Icon> },
  { to: '/reference', label: 'Rules and data', icon: <Icon><path d="M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3Z" /><path d="M5 17a3 3 0 0 1 3-3h11" /></Icon> },
];

type Theme = 'dark' | 'light';
const THEME_KEY = 'ific-guard-theme';

function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(() => (document.documentElement.dataset.theme === 'light' ? 'light' : 'dark'));
  useEffect(() => {
    if (theme === 'light') document.documentElement.dataset.theme = 'light';
    else delete document.documentElement.dataset.theme;
    try { localStorage.setItem(THEME_KEY, theme); } catch { /* the choice lasts until the page closes */ }
  }, [theme]);
  const next = theme === 'dark' ? 'light' : 'dark';
  return (
    <button type="button" className="theme-toggle" onClick={() => setTheme(next)} aria-label={`Switch to ${next} theme`}>
      <Icon>{theme === 'dark' ? <><circle cx="12" cy="12" r="4" /><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6 7 7M17 17l1.4 1.4M5.6 18.4 7 17M17 7l1.4-1.4" /></> : <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z" />}</Icon>
      <span>{next === 'light' ? 'Light theme' : 'Dark theme'}</span>
    </button>
  );
}

export function Layout() {
  const { pathname } = useLocation();
  useEffect(() => {
    window.scrollTo(0, 0); // Newer browsers return a Promise here, which must not reach React as a cleanup.
  }, [pathname]);
  return (
    <div className="shell">
      <a className="skip" href="#main">Skip to content</a>
      <aside className="sidebar">
        <NavLink to="/" className="brand">
          <span className="brand-mark" aria-hidden="true"><i /><i /><i /><i /></span>
          IFIC Guard
        </NavLink>
        <nav aria-label="Main">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end}>{n.icon}<span>{n.label}</span></NavLink>
          ))}
        </nav>
        <div className="sidebar-foot">
          <NavLink to="/analyze" className="btn btn-primary sidebar-cta">Analyse a file</NavLink>
          <ThemeToggle />
        </div>
      </aside>
      <div className="content">
        <main id="main" className="page">
          {api.mode === 'sample' ? (
            <p className="context-note context-warn" role="note"><strong>Sample mode.</strong> Results come from a one-off screen of real ITU files, not the finished pipeline. They’re prompts for review, not regulatory findings.</p>
          ) : (
            <p className="context-note" role="note">Cases are possible concerns for expert review. None has confirmed that a limit is exceeded unless it shows a calculation.</p>
          )}
          <Outlet />
        </main>
      </div>
    </div>
  );
}
