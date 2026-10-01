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
            'inline-flex items-center gap-1.5 px-2 py-0.5 text-xs font-machine uppercase tracking-widest border border-grey-500 texture-hatch text-pure',
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
            'inline-flex items-center gap-1.5 px-2 py-0.5 text-xs font-machine uppercase tracking-widest border border-paper bg-canvas text-pure',
            className
          )
        )}
      >
        <span className="w-1.5 h-1.5 rounded-full bg-pure" />
        <span>{status.replace(/_/g, ' ')}</span>
      </span>
    );
  }

  if (norm.includes('waiting') || norm.includes('awaiting')) {
    return (
      <span
        className={twMerge(
          clsx(
            'inline-flex items-center gap-1.5 px-2 py-0.5 text-xs font-machine uppercase tracking-widest border border-grey-700 bg-canvas text-grey-300',
            className
          )
        )}
      >
        <span className="w-1.5 h-1.5 rounded-full border border-grey-300" />
        <span>{status.replace(/_/g, ' ')}</span>
      </span>
    );
  }

  if (norm === 'complete' || norm === 'completed' || norm === 'resolved') {
    return (
      <span
        className={twMerge(
          clsx(
            'inline-flex items-center gap-1.5 px-2 py-0.5 text-xs font-machine uppercase tracking-widest border border-pure bg-pure text-canvas font-semibold',
            className
          )
        )}
      >
        <span className="w-1.5 h-1.5 bg-canvas" />
        <span>{status.replace(/_/g, ' ')}</span>
      </span>
    );
  }

  if (norm === 'cancelled' || norm === 'failed' || norm === 'rejected') {
    return (
      <span
        className={twMerge(
          clsx(
            'inline-flex items-center gap-1.5 px-2 py-0.5 text-xs font-machine uppercase tracking-widest border border-grey-700 text-grey-500',
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
          'inline-flex items-center gap-1.5 px-2 py-0.5 text-xs font-machine uppercase tracking-widest border border-grey-700 text-grey-300',
          className
        )
      )}
    >
      <span className="text-[10px]">┄</span>
      <span>{status.replace(/_/g, ' ')}</span>
    </span>
  );
};
