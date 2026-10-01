import React from 'react';

interface AgentGlyphProps {
  active?: boolean;
  size?: 'sm' | 'md' | 'lg';
  ariaLabel?: string;
}

export const AgentGlyph: React.FC<AgentGlyphProps> = ({
  active = false,
  size = 'md',
  ariaLabel = 'System activity indicator',
}) => {
  const dimension = size === 'sm' ? 'w-4 h-4' : size === 'lg' ? 'w-8 h-8' : 'w-6 h-6';

  return (
    <div
      role="status"
      aria-label={ariaLabel}
      className={`relative inline-flex items-center justify-center shrink-0 ${dimension}`}
    >
      {/* Outer concentric square */}
      <div
        className={`absolute inset-0 border border-jv-rule transition-transform duration-700 ${
          active ? 'rotate-45 scale-90 border-jv-ink' : 'rotate-0'
        }`}
      />
      {/* Inner concentric core */}
      <div
        className={`w-1.5 h-1.5 bg-jv-ink transition-all duration-300 ${
          active ? 'scale-125 animate-pulse' : 'scale-75 opacity-60'
        }`}
      />
    </div>
  );
};
