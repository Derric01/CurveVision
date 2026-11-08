import { Moon, Sun } from 'lucide-react';
import { useTheme } from './theme';

/** Switches between pitch black and white; says which one it will switch to. */
export function ThemeToggle({ className }: { className?: string }) {
  const { theme, toggle } = useTheme();
  const next = theme === 'dark' ? 'light' : 'dark';
  return (
    <button
      type="button"
      onClick={toggle}
      className={
        'inline-flex h-8 w-8 items-center justify-center rounded-full text-ink-400 ' +
        'transition-colors hover:bg-ink-850 hover:text-ink-100 ' +
        (className ?? '')
      }
      title={`Switch to the ${next} theme`}
      aria-label={`Switch to the ${next} theme`}
      data-theme-toggle={next}
    >
      {theme === 'dark' ? <Sun size={15} /> : <Moon size={15} />}
    </button>
  );
}
