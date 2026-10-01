import React, { useState, useEffect, useRef } from 'react';

interface NumberRollProps {
  value: number | string;
  className?: string;
  formatPad?: number; // e.g. 2 for "02"
}

interface RollState {
  from: string;
  to: string;
  active: boolean;
}

export const NumberRoll: React.FC<NumberRollProps> = ({
  value,
  className = '',
  formatPad = 0,
}) => {
  const formatted =
    typeof value === 'number' && formatPad > 0
      ? String(value).padStart(formatPad, '0')
      : String(value);

  const [currentVal, setCurrentVal] = useState(formatted);
  const [rollState, setRollState] = useState<RollState | null>(null);
  const isFirstRender = useRef(true);
  const rafRef = useRef<number | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const completionTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (isFirstRender.current) {
      isFirstRender.current = false;
      return;
    }

    if (formatted !== currentVal) {
      // Clear any pending animation timers/frames
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      if (timerRef.current) clearTimeout(timerRef.current);
      if (completionTimerRef.current) clearTimeout(completionTimerRef.current);

      const prefersReducedMotion =
        typeof window !== 'undefined' &&
        Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches);

      if (prefersReducedMotion) {
        timerRef.current = setTimeout(() => {
          setCurrentVal(formatted);
          setRollState(null);
        }, 0);
        return;
      }

      const previous = currentVal;
      timerRef.current = setTimeout(() => {
        // Phase 1: Mount the incoming layer at translateY(100%), old layer at translateY(0)
        setRollState({
          from: previous,
          to: formatted,
          active: false,
        });

        // Phase 2: Next frame, activate transition (old -> -100%, new -> 0)
        rafRef.current = requestAnimationFrame(() => {
          rafRef.current = requestAnimationFrame(() => {
            setRollState({
              from: previous,
              to: formatted,
              active: true,
            });
          });
        });
      }, 0);

      // Phase 3: Transition completion (220ms matches --motion-base)
      completionTimerRef.current = setTimeout(() => {
        setCurrentVal(formatted);
        setRollState(null);
      }, 220);
    }
  }, [formatted, currentVal]);

  useEffect(() => {
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      if (timerRef.current) clearTimeout(timerRef.current);
      if (completionTimerRef.current) clearTimeout(completionTimerRef.current);
    };
  }, []);

  return (
    <span className={`relative inline-block overflow-hidden align-baseline ${className}`}>
      {rollState ? (
        <>
          {/* Old value rolling upwards to -100% */}
          <span
            className={`absolute inset-0 inline-block font-machine pointer-events-none transition-[transform,opacity] duration-base ease-editorial ${
              rollState.active ? '-translate-y-full opacity-0' : 'translate-y-0 opacity-100'
            }`}
            aria-hidden="true"
          >
            {rollState.from}
          </span>
          {/* New value rolling upwards into position from 100% to 0 */}
          <span
            className={`inline-block font-machine transition-[transform,opacity] duration-base ease-editorial ${
              rollState.active ? 'translate-y-0 opacity-100' : 'translate-y-full opacity-0'
            }`}
          >
            {rollState.to}
          </span>
        </>
      ) : (
        <span className="inline-block font-machine">
          {currentVal}
        </span>
      )}
    </span>
  );
};
