import React, { useEffect, useRef } from 'react';

interface AmbientCanvasProps {
  hasPendingApprovals?: boolean;
}

export const AmbientCanvas: React.FC<AmbientCanvasProps> = ({ hasPendingApprovals = false }) => {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const prefersReducedMotion =
      typeof window !== 'undefined' &&
      window.matchMedia &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    let animationFrameId: number | null = null;
    let isRunning = false;
    let width = 0;
    let height = 0;
    const dpr = Math.min(typeof window !== 'undefined' ? window.devicePixelRatio || 1 : 1, 2);

    let mouseX = -9999;
    let mouseY = -9999;
    let isMoving = false;
    let moveTimeout: ReturnType<typeof setTimeout> | null = null;

    const spacing = 110; // Wide editorial registration mark spacing
    interface Point {
      x: number;
      y: number;
      curX: number;
      curY: number;
    }
    let points: Point[] = [];

    const renderStatic = () => {
      ctx.clearRect(0, 0, width, height);
      const isInk = typeof document !== 'undefined' && document.documentElement.dataset.theme === 'ink';
      const markColor = isInk ? 'rgba(242, 240, 233, 0.06)' : 'rgba(10, 10, 9, 0.06)';

      ctx.strokeStyle = markColor;
      ctx.lineWidth = 1;

      const size = 3;
      for (const p of points) {
        ctx.beginPath();
        ctx.moveTo(p.x - size, p.y);
        ctx.lineTo(p.x + size, p.y);
        ctx.moveTo(p.x, p.y - size);
        ctx.lineTo(p.x + size, p.y);
        ctx.stroke();
      }
    };

    const stopLoop = () => {
      isRunning = false;
      if (animationFrameId !== null) {
        cancelAnimationFrame(animationFrameId);
        animationFrameId = null;
      }
    };

    const loop = () => {
      if (document.hidden) {
        stopLoop();
        return;
      }

      ctx.clearRect(0, 0, width, height);
      const isInk = typeof document !== 'undefined' && document.documentElement.dataset.theme === 'ink';
      const baseAlpha = hasPendingApprovals ? 0.08 : 0.05;
      const markColor = isInk
        ? `rgba(242, 240, 233, ${baseAlpha})`
        : `rgba(10, 10, 9, ${baseAlpha})`;

      ctx.strokeStyle = markColor;
      ctx.lineWidth = 1;

      const size = 3;
      let maxDisplacement = 0;

      for (const p of points) {
        const dx = mouseX - p.x;
        const dy = mouseY - p.y;
        const dist = Math.hypot(dx, dy);

        let targetX = p.x;
        let targetY = p.y;

        if (dist < 140) {
          const force = (1 - dist / 140) * 3;
          targetX = p.x - (dx / (dist || 1)) * force;
          targetY = p.y - (dy / (dist || 1)) * force;
        }

        p.curX += (targetX - p.curX) * 0.12;
        p.curY += (targetY - p.curY) * 0.12;

        const disp = Math.hypot(p.curX - p.x, p.curY - p.y);
        if (disp > maxDisplacement) {
          maxDisplacement = disp;
        }

        ctx.beginPath();
        ctx.moveTo(p.curX - size, p.curY);
        ctx.lineTo(p.curX + size, p.curY);
        ctx.moveTo(p.curX, p.curY - size);
        ctx.lineTo(p.curX + size, p.curY);
        ctx.stroke();
      }

      // If pointer is idle/offscreen and all points have settled to resting position (< 0.05px)
      if (!isMoving && maxDisplacement < 0.05) {
        for (const p of points) {
          p.curX = p.x;
          p.curY = p.y;
        }
        renderStatic();
        stopLoop();
        return;
      }

      animationFrameId = requestAnimationFrame(loop);
    };

    const startLoop = () => {
      if (isRunning || prefersReducedMotion || (typeof document !== 'undefined' && document.hidden)) return;
      isRunning = true;
      animationFrameId = requestAnimationFrame(loop);
    };

    const resize = () => {
      width = window.innerWidth;
      height = window.innerHeight;
      canvas.width = Math.floor(width * dpr);
      canvas.height = Math.floor(height * dpr);
      ctx.scale(dpr, dpr);

      points = [];
      const cols = Math.ceil(width / spacing);
      const rows = Math.ceil(height / spacing);
      for (let c = 1; c < cols; c++) {
        for (let r = 1; r < rows; r++) {
          const x = c * spacing;
          const y = r * spacing;
          points.push({ x, y, curX: x, curY: y });
        }
      }

      renderStatic();
      if (!prefersReducedMotion && (typeof document === 'undefined' || !document.hidden)) {
        startLoop();
      }
    };

    const handlePointerMove = (e: PointerEvent) => {
      mouseX = e.clientX;
      mouseY = e.clientY;
      isMoving = true;
      if (moveTimeout) clearTimeout(moveTimeout);
      moveTimeout = setTimeout(() => {
        isMoving = false;
      }, 150);
      startLoop();
    };

    const handlePointerLeave = () => {
      mouseX = -9999;
      mouseY = -9999;
      isMoving = false;
      if (moveTimeout) clearTimeout(moveTimeout);
      startLoop();
    };

    const handleVisibilityChange = () => {
      if (document.hidden) {
        stopLoop();
      } else {
        startLoop();
      }
    };

    window.addEventListener('resize', resize, { passive: true });
    window.addEventListener('pointermove', handlePointerMove, { passive: true });
    window.addEventListener('pointerleave', handlePointerLeave, { passive: true });
    document.addEventListener('visibilitychange', handleVisibilityChange);

    resize();

    return () => {
      window.removeEventListener('resize', resize);
      window.removeEventListener('pointermove', handlePointerMove);
      window.removeEventListener('pointerleave', handlePointerLeave);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      if (moveTimeout) clearTimeout(moveTimeout);
      stopLoop();
    };
  }, [hasPendingApprovals]);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden="true"
      className="fixed inset-0 pointer-events-none -z-10 w-full h-full select-none"
    />
  );
};
