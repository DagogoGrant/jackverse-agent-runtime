import React from 'react';
import { useTheme } from '../../hooks/useTheme';

interface ThemeSwitchProps {
  className?: string;
}

export const ThemeSwitch: React.FC<ThemeSwitchProps> = ({ className = '' }) => {
  const { toggleTheme, isPaper } = useTheme();

  return (
    <button
      type="button"
      onClick={toggleTheme}
      className={`inline-flex items-center gap-2 px-2.5 py-1 text-xs font-machine uppercase tracking-wider border border-jv-rule bg-jv-surface text-jv-ink hover:border-jv-rule-strong active:scale-tactile transition-colors select-none ${className}`}
      aria-label={`Visual identity: ${isPaper ? 'Paper' : 'Ink'}. Switch to ${isPaper ? 'Ink' : 'Paper'}`}
      title={`Switch to ${isPaper ? 'Ink' : 'Paper'} Mode`}
    >
      <span className="text-[13px] leading-none" aria-hidden="true">
        {isPaper ? '◐' : '◑'}
      </span>
      <span className="flex items-center gap-1">
        <span
          className={
            isPaper
              ? 'font-bold text-jv-ink underline decoration-1 underline-offset-2'
              : 'text-jv-muted'
          }
        >
          PAPER
        </span>
        <span className="text-jv-muted/40">/</span>
        <span
          className={
            !isPaper
              ? 'font-bold text-jv-ink underline decoration-1 underline-offset-2'
              : 'text-jv-muted'
          }
        >
          INK
        </span>
      </span>
    </button>
  );
};
