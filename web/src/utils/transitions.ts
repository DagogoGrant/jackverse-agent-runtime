/**
 * Progressive enhancement wrapper for native View Transitions API.
 * Safely falls back to immediate execution if unsupported or if user prefers reduced motion.
 */
export function startViewTransition(fn: () => void | Promise<void>): void {
  const prefersReducedMotion =
    typeof window !== 'undefined' &&
    window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  if (
    !prefersReducedMotion &&
    typeof document !== 'undefined' &&
    'startViewTransition' in document &&
    typeof (document as any).startViewTransition === 'function'
  ) {
    (document as any).startViewTransition(fn);
  } else {
    fn();
  }
}
