/**
 * The two themes: pitch black (the default) and white. The choice is one attribute on
 * <html>, `data-theme`, which the CSS variables in `index.css` follow; it is remembered
 * per browser, and applied before the first paint so the page never flashes the other one.
 */

import { useCallback, useEffect, useState } from 'react';

export type Theme = 'dark' | 'light';

export const THEME_KEY = 'curvevision.theme';

/** What a stored value means. Anything unrecognised, or nothing, is the default: dark. */
export function parseTheme(stored: string | null | undefined): Theme {
  return stored === 'light' ? 'light' : 'dark';
}

export function otherTheme(theme: Theme): Theme {
  return theme === 'dark' ? 'light' : 'dark';
}

function stored(): Theme {
  try {
    return parseTheme(window.localStorage.getItem(THEME_KEY));
  } catch {
    // Storage can be refused (a private window, a locked-down profile): dark it is.
    return 'dark';
  }
}

export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
}

/** Called once, before React renders. */
export function applyStoredTheme(): void {
  applyTheme(stored());
}

export function useTheme(): { theme: Theme; toggle: () => void } {
  const [theme, setTheme] = useState<Theme>(stored);
  useEffect(() => applyTheme(theme), [theme]);
  const toggle = useCallback(() => {
    setTheme((current) => {
      const next = otherTheme(current);
      try {
        window.localStorage.setItem(THEME_KEY, next);
      } catch {
        // Not remembered, but still switched for this visit.
      }
      return next;
    });
  }, []);
  return { theme, toggle };
}
