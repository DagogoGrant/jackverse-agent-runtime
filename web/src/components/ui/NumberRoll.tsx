import React, { useState, useEffect, useRef } from 'react';

interface NumberRollProps {
  value: number | string;
  className?: string;
  formatPad?: number; // e.g. 2 for "02"
}

export const NumberRoll: React.FC<NumberRollProps> = ({
  value,
  className = '',
  formatPad = 0,
}) => {
  const formatted = typeof value === 'number' && formatPad > 0
    ? String(value).padStart(formatPad, '0')
    : String(value);

  const [currentVal, setCurrentVal] = useState(formatted);
  const [prevVal, setPrevVal] = useState<string | null>(null);
  const [isAnimating, setIsAnimating] = useState(false);
  const isFirstRender = useRef(true);

  useEffect(() => {
    if (isFirstRender.current) {
      isFirstRender.current = false;
      return;
    }

    if (formatted !== currentVal) {
      const prefersReducedMotion =
        typeof window !== 'undefined' &&
        window.matchMedia &&
        window.matchMedia('(prefers-reduced-motion: reduce)').matches;

      if (prefersReducedMotion) {
        const timer = setTimeout(() => {
          setCurrentVal(formatted);
        }, 0);
        return () => clearTimeout(timer);
      }

      const timer1 = setTimeout(() => {
        setPrevVal(currentVal);
        setCurrentVal(formatted);
        setIsAnimating(true);
      }, 0);

      const timer2 = setTimeout(() => {
        setIsAnimating(false);
        setPrevVal(null);
      }, 220);

      return () => {
        clearTimeout(timer1);
        clearTimeout(timer2);
      };
    }
  }, [formatted, currentVal]);

  return (
    <span className={`relative inline-block overflow-hidden align-baseline ${className}`}>
      {isAnimating && prevVal !== null && (
        <span
          className="absolute inset-0 inline-block font-machine transition-transform duration-base ease-editorial -translate-y-full opacity-0"
          aria-hidden="true"
        >
          {prevVal}
        </span>
      )}
      <span
        className={`inline-block font-machine transition-all duration-base ease-editorial ${
          isAnimating ? 'translate-y-0 opacity-100' : ''
        }`}
      >
        {currentVal}
      </span>
    </span>
  );
};
