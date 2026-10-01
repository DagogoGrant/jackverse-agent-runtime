import { useState, useEffect, useCallback, useRef } from 'react';

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
  const domTheme = typeof document !== 'undefined' ? document.documentElement.dataset.theme : undefined;
  if (domTheme === 'ink' || domTheme === 'paper') {
    return domTheme;
  }
  return 'paper';
}

export function useTheme() {
  const [theme, setThemeState] = useState<Theme>(getInitialTheme);
  const washTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (washTimerRef.current) {
        clearTimeout(washTimerRef.current);
      }
    };
  }, []);

  useEffect(() => {
    // Ensure external DOM reflects initial theme
    const current = getInitialTheme();
    if (typeof document !== 'undefined') {
      document.documentElement.dataset.theme = current;
    }

    const handleSync = () => {
      const activeTheme = (typeof document !== 'undefined' ? document.documentElement.dataset.theme as Theme : undefined) || getInitialTheme();
      if (activeTheme === 'paper' || activeTheme === 'ink') {
        setThemeState(activeTheme);
      }
    };

    let observer: MutationObserver | null = null;
    if (typeof document !== 'undefined') {
      observer = new MutationObserver((mutations) => {
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
    }

    window.addEventListener('storage', handleSync);

    return () => {
      observer?.disconnect();
      window.removeEventListener('storage', handleSync);
    };
  }, []);

  const setTheme = useCallback((newTheme: Theme) => {
    const prefersReducedMotion =
      typeof window !== 'undefined' &&
      Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches);

    try {
      localStorage.setItem(STORAGE_KEY, newTheme);
    } catch {
      // Ignore storage errors
    }

    if (washTimerRef.current) {
      clearTimeout(washTimerRef.current);
      washTimerRef.current = null;
    }

    if (prefersReducedMotion) {
      if (typeof document !== 'undefined') {
        document.documentElement.classList.remove('theme-wash');
        document.documentElement.dataset.theme = newTheme;
      }
      setThemeState(newTheme);
      return;
    }

    // Apply temporary theme wash class for authored 400ms transition
    if (typeof document !== 'undefined') {
      document.documentElement.classList.add('theme-wash');
      document.documentElement.dataset.theme = newTheme;
    }
    setThemeState(newTheme);

    washTimerRef.current = setTimeout(() => {
      if (typeof document !== 'undefined') {
        document.documentElement.classList.remove('theme-wash');
      }
      washTimerRef.current = null;
    }, 450);
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
