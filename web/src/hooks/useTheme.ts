import { useState, useEffect, useCallback } from 'react';

export type Theme = 'paper' | 'ink';

const STORAGE_KEY = 'jv_theme';

export function getInitialTheme(): Theme {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === 'ink' || stored === 'paper') {
      return stored;
    }
  } catch {
    // Ignore storage errors
  }
  const domTheme = document.documentElement.dataset.theme;
  if (domTheme === 'ink' || domTheme === 'paper') {
    return domTheme;
  }
  return 'paper';
}

export function useTheme() {
  const [theme, setThemeState] = useState<Theme>(getInitialTheme);

  useEffect(() => {
    // Ensure external DOM reflects initial theme
    const current = getInitialTheme();
    document.documentElement.dataset.theme = current;

    const handleSync = () => {
      const activeTheme = (document.documentElement.dataset.theme as Theme) || getInitialTheme();
      if (activeTheme === 'paper' || activeTheme === 'ink') {
        setThemeState(activeTheme);
      }
    };

    const observer = new MutationObserver((mutations) => {
      for (const m of mutations) {
        if (m.type === 'attributes' && m.attributeName === 'data-theme') {
          handleSync();
        }
      }
    });

    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme'],
    });

    window.addEventListener('storage', handleSync);

    return () => {
      observer.disconnect();
      window.removeEventListener('storage', handleSync);
    };
  }, []);

  const setTheme = useCallback((newTheme: Theme) => {
    const prefersReducedMotion =
      typeof window !== 'undefined' &&
      window.matchMedia &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    try {
      localStorage.setItem(STORAGE_KEY, newTheme);
    } catch {
      // Ignore storage errors
    }

    if (prefersReducedMotion) {
      document.documentElement.dataset.theme = newTheme;
      setThemeState(newTheme);
      return;
    }

    // Apply temporary theme wash class for authored 400ms transition
    document.documentElement.classList.add('theme-wash');
    document.documentElement.dataset.theme = newTheme;
    setThemeState(newTheme);

    const timer = setTimeout(() => {
      document.documentElement.classList.remove('theme-wash');
    }, 450);

    return () => clearTimeout(timer);
  }, []);

  const toggleTheme = useCallback(() => {
    setTheme(theme === 'paper' ? 'ink' : 'paper');
  }, [theme, setTheme]);

  return {
    theme,
    isPaper: theme === 'paper',
    isInk: theme === 'ink',
    setTheme,
    toggleTheme,
  };
}
