import React from 'react';
import { clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

interface TextureBadgeProps {
  status: string;
  className?: string;
}

export const TextureBadge: React.FC<TextureBadgeProps> = ({ status, className }) => {
  const norm = status.toLowerCase().replace(/_/g, ' ');

  if (norm.includes('blocked')) {
    return (
      <span
        className={twMerge(
          clsx(
            'inline-flex items-center gap-1.5 px-2 py-0.5 text-xs font-machine uppercase tracking-widest border border-jv-rule-strong texture-hatch text-jv-ink select-none',
            className
          )
        )}
      >
        <span className="font-bold">///</span>
        <span>Blocked</span>
      </span>
    );
  }

  if (norm === 'active' || norm === 'investigating' || norm === 'action in progress') {
    return (
      <span
        className={twMerge(
          clsx(
            'inline-flex items-center gap-1.5 px-2 py-0.5 text-xs font-machine uppercase tracking-widest border border-jv-ink bg-jv-bg text-jv-ink select-none',
            className
          )
        )}
      >
        <span className="w-1.5 h-1.5 bg-jv-ink inline-block" />
        <span>{status.replace(/_/g, ' ')}</span>
      </span>
    );
  }

  if (norm.includes('waiting') || norm.includes('awaiting')) {
    return (
      <span
        className={twMerge(
          clsx(
            'inline-flex items-center gap-1.5 px-2 py-0.5 text-xs font-machine uppercase tracking-widest border border-jv-rule bg-jv-surface text-jv-ink-soft select-none',
            className
          )
        )}
      >
        <span className="w-1.5 h-1.5 border border-jv-ink-soft inline-block" />
        <span>{status.replace(/_/g, ' ')}</span>
      </span>
    );
  }

  if (norm === 'complete' || norm === 'completed' || norm === 'resolved') {
    return (
      <span
        className={twMerge(
          clsx(
            'inline-flex items-center gap-1.5 px-2 py-0.5 text-xs font-machine uppercase tracking-widest border border-jv-ink bg-jv-ink text-jv-bg font-semibold select-none',
            className
          )
        )}
      >
        <span className="w-1.5 h-1.5 bg-jv-bg inline-block" />
        <span>{status.replace(/_/g, ' ')}</span>
      </span>
    );
  }

  if (norm === 'cancelled' || norm === 'failed' || norm === 'rejected') {
    return (
      <span
        className={twMerge(
          clsx(
            'inline-flex items-center gap-1.5 px-2 py-0.5 text-xs font-machine uppercase tracking-widest border border-jv-rule text-jv-muted select-none',
            className
          )
        )}
      >
        <span>✕</span>
        <span>{status.replace(/_/g, ' ')}</span>
      </span>
    );
  }

  // Default / Draft / Evaluated
  return (
    <span
      className={twMerge(
        clsx(
          'inline-flex items-center gap-1.5 px-2 py-0.5 text-xs font-machine uppercase tracking-widest border border-jv-rule text-jv-ink-soft select-none',
          className
        )
      )}
    >
      <span className="text-[10px]">┄</span>
      <span>{status.replace(/_/g, ' ')}</span>
    </span>
  );
};
