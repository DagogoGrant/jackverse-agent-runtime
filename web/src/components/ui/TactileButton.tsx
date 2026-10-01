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
    'relative inline-flex items-center justify-center font-interface font-medium transition-all duration-150 ease-out select-none focus:outline-none focus-visible:ring-2 focus-visible:ring-paper active:scale-tactile disabled:opacity-40 disabled:cursor-not-allowed disabled:active:scale-100';

  const variants = {
    // Primary: Pure white surface -> Canvas black surface on hover
    primary:
      'bg-pure text-canvas border border-pure hover:bg-canvas hover:text-pure active:bg-ink',
    // Secondary: Canvas black surface with hairline -> pure white on hover
    secondary:
      'bg-canvas text-paper border border-grey-700 hover:border-paper hover:text-pure',
    // Outline: Minimal hairline
    outline:
      'bg-transparent text-paper border border-grey-700 hover:border-paper hover:bg-ink',
    // Danger: Stark white text with heavy hairline border
    danger:
      'bg-canvas text-paper border border-grey-500 hover:border-paper hover:bg-ink',
  };

  const sizes = {
    sm: 'text-xs px-2.5 py-1 tracking-wider uppercase',
    md: 'text-sm px-4 py-2 tracking-wide',
    lg: 'text-base px-6 py-3 tracking-wide',
  };

  return (
    <button
      className={twMerge(clsx(baseStyles, variants[variant], sizes[size], className))}
      disabled={disabled || loading}
      {...props}
    >
      {loading ? (
        <span className="inline-flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-current animate-ping" />
          <span>Processing...</span>
        </span>
      ) : (
        children
      )}
    </button>
  );
};
