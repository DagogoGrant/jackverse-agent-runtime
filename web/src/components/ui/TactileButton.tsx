import React from 'react';
import { clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

export interface TactileButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'outline' | 'danger';
  size?: 'sm' | 'md' | 'lg';
  loading?: boolean;
}

export const TactileButton: React.FC<TactileButtonProps> = ({
  children,
  variant = 'primary',
  size = 'md',
  loading = false,
  className,
  disabled,
  ...props
}) => {
  const baseStyles =
    'relative inline-flex items-center justify-center font-interface font-medium transition-colors duration-fast ease-editorial select-none focus:outline-none focus-visible:ring-2 focus-visible:ring-jv-ink active:scale-tactile disabled:opacity-40 disabled:cursor-not-allowed disabled:active:scale-100';

  const variants = {
    // Primary: Solid ink surface with inverted text, reverses on hover
    primary:
      'bg-jv-ink text-jv-bg border border-jv-ink hover:bg-jv-bg hover:text-jv-ink active:bg-jv-surface',
    // Secondary: Elevated surface with subtle hairline
    secondary:
      'bg-jv-surface text-jv-ink border border-jv-rule hover:border-jv-rule-strong hover:text-jv-ink',
    // Outline: Minimal transparent background with hairline
    outline:
      'bg-transparent text-jv-ink border border-jv-rule hover:border-jv-ink hover:bg-jv-surface',
    // Danger: Stark border with tactile response
    danger:
      'bg-jv-bg text-jv-ink border border-jv-rule-strong hover:border-jv-ink hover:bg-jv-surface',
  };

  const sizes = {
    sm: 'text-xs px-2.5 py-1 tracking-wider uppercase font-machine',
    md: 'text-sm px-4 py-2 tracking-wide font-interface',
    lg: 'text-base px-6 py-3 tracking-wide font-interface',
  };

  return (
    <button
      className={twMerge(clsx(baseStyles, variants[variant], sizes[size], className))}
      disabled={disabled || loading}
      {...props}
    >
      {loading ? (
        <span className="inline-flex items-center gap-2">
          <span className="w-1.5 h-1.5 rounded-full bg-current animate-ping" />
          <span className="font-machine text-xs">Processing...</span>
        </span>
      ) : (
        children
      )}
    </button>
  );
};
