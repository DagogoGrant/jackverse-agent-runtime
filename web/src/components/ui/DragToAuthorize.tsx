import React, { useState, useRef, useCallback } from 'react';
import { ArrowRight, Check, ShieldAlert } from 'lucide-react';
import { TactileButton } from './TactileButton';

interface DragToAuthorizeProps {
  onAuthorize: () => Promise<void> | void;
  disabled?: boolean;
  label?: string;
  consequentialDescription?: string;
}

export const DragToAuthorize: React.FC<DragToAuthorizeProps> = ({
  onAuthorize,
  disabled = false,
  label = 'Drag to authorize action',
  consequentialDescription,
}) => {
  const [dragProgress, setDragProgress] = useState(0); // 0 to 1
  const [isDragging, setIsDragging] = useState(false);
  const [isAuthorized, setIsAuthorized] = useState(false);
  const [isPending, setIsPending] = useState(false);
  const [showStandardFallback, setShowStandardFallback] = useState(false);

  const containerRef = useRef<HTMLDivElement>(null);
  const startXRef = useRef<number>(0);
  const currentDragRef = useRef<number>(0);

  const executeAuthorize = useCallback(async () => {
    if (isAuthorized || isPending || disabled) return;
    setIsAuthorized(true);
    setIsPending(true);
    try {
      await onAuthorize();
    } catch (err) {
      setIsAuthorized(false);
      setDragProgress(0);
      throw err;
    } finally {
      setIsPending(false);
    }
  }, [isAuthorized, isPending, disabled, onAuthorize]);

  const handlePointerDown = (e: React.PointerEvent) => {
    if (disabled || isAuthorized || isPending) return;
    setIsDragging(true);
    startXRef.current = typeof e.clientX === 'number' && !isNaN(e.clientX) ? e.clientX : 0;
    currentDragRef.current = 0;
    (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
  };

  const handlePointerMove = (e: React.PointerEvent) => {
    if (!isDragging || !containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    const maxDrag = Math.max(1, (rect.width || 400) - 48); // thumb width approx 48px
    const currentX = typeof e.clientX === 'number' && !isNaN(e.clientX) ? e.clientX : startXRef.current;
    const delta = Math.max(0, Math.min(currentX - startXRef.current, maxDrag));
    const rawProgress = maxDrag > 0 ? delta / maxDrag : 0;
    const progress = Math.max(0, Math.min(1, isNaN(rawProgress) ? 0 : rawProgress));
    currentDragRef.current = progress;
    setDragProgress(progress);

    if (progress >= 0.88) {
      // Threshold reached!
      setIsDragging(false);
      setDragProgress(1);
      executeAuthorize();
    }
  };

  const handlePointerUp = () => {
    if (!isDragging) return;
    setIsDragging(false);
    if (currentDragRef.current < 0.88) {
      // Spring reset
      setDragProgress(0);
      currentDragRef.current = 0;
    }
  };

  // Keyboard accessibility: Enter or Space triggers step-by-step or instant confirmation
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (disabled || isAuthorized || isPending) return;
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      executeAuthorize();
    }
  };

  return (
    <div className="w-full space-y-3 font-interface">
      {consequentialDescription && (
        <div className="flex items-start gap-2 p-3 border border-grey-700 bg-ink text-xs text-grey-300">
          <ShieldAlert className="w-4 h-4 text-paper shrink-0 mt-0.5" />
          <span>{consequentialDescription}</span>
        </div>
      )}

      {showStandardFallback ? (
        <div className="flex items-center gap-3">
          <TactileButton
            variant="primary"
            size="lg"
            className="flex-1 tracking-widest uppercase font-machine"
            disabled={disabled || isPending}
            loading={isPending}
            onClick={executeAuthorize}
          >
            Authorize Consequential Action →
          </TactileButton>
          <button
            type="button"
            className="text-xs text-grey-500 hover:text-paper underline"
            onClick={() => setShowStandardFallback(false)}
          >
            Use slide control
          </button>
        </div>
      ) : (
        <div>
          <div
            ref={containerRef}
            tabIndex={disabled || isAuthorized ? -1 : 0}
            role="slider"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round((dragProgress || 0) * 100)}
            aria-label={label}
            onKeyDown={handleKeyDown}
            onPointerDown={handlePointerDown}
            onPointerMove={handlePointerMove}
            onPointerUp={handlePointerUp}
            onPointerCancel={handlePointerUp}
            className={`relative h-14 w-full select-none overflow-hidden border border-grey-700 bg-ink transition-colors focus:outline-none focus:border-paper ${
              disabled ? 'opacity-40 cursor-not-allowed' : 'cursor-grab active:cursor-grabbing'
            }`}
          >
            {/* Background track line and text */}
            <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
              <span className="font-machine text-xs tracking-widest text-grey-500 uppercase">
                {isAuthorized ? 'Authorized' : isPending ? 'Authorizing...' : label}
              </span>
            </div>

            {/* Visual travel progress bar */}
            <div
              className="absolute left-0 top-0 bottom-0 bg-grey-700/50 transition-all duration-75"
              style={{ width: `${(dragProgress || 0) * 100}%` }}
            />

            {/* Sliding Thumb */}
            <div
              className={`absolute top-1 bottom-1 w-12 flex items-center justify-center transition-all ${
                isDragging ? 'duration-0' : 'duration-200'
              } ${
                isAuthorized ? 'bg-pure text-canvas' : 'bg-paper text-canvas border border-pure'
              }`}
              style={{
                left: `calc(${(dragProgress || 0) * 100}% - ${(dragProgress || 0) * 48}px + 4px)`,
              }}
            >
              {isAuthorized ? (
                <Check className="w-5 h-5 stroke-[2.5]" />
              ) : (
                <ArrowRight className="w-5 h-5 stroke-[2]" />
              )}
            </div>
          </div>

          <div className="mt-2 flex justify-between items-center text-[11px] font-machine text-grey-500">
            <span>Press Space / Enter or slide to confirm</span>
            <button
              type="button"
              className="hover:text-paper underline"
              onClick={() => setShowStandardFallback(true)}
            >
              Standard button fallback
            </button>
          </div>
        </div>
      )}
    </div>
  );
};
