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
    let width = 0;
    let height = 0;
    const dpr = Math.min(typeof window !== 'undefined' ? window.devicePixelRatio || 1 : 1, 2);

    let mouseX = -9999;
    let mouseY = -9999;
    let isMoving = false;
    let idleTicks = 0;

    const spacing = 110; // Wide editorial registration mark spacing
    interface Point {
      x: number;
      y: number;
      curX: number;
      curY: number;
    }
    let points: Point[] = [];

    const resize = () => {
      width = window.innerWidth;
      height = window.innerHeight;
      canvas.width = Math.floor(width * dpr);
      canvas.height = Math.floor(height * dpr);
      ctx.scale(dpr, dpr);

      // Generate quiet grid points
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

      if (prefersReducedMotion) {
        renderStatic();
      }
    };

    const handlePointerMove = (e: PointerEvent) => {
      mouseX = e.clientX;
      mouseY = e.clientY;
      isMoving = true;
      idleTicks = 0;
    };

    const handlePointerLeave = () => {
      mouseX = -9999;
      mouseY = -9999;
      isMoving = false;
    };

    const renderStatic = () => {
      ctx.clearRect(0, 0, width, height);
      // Read current ink color token from root
      const isInk = document.documentElement.dataset.theme === 'ink';
      const markColor = isInk ? 'rgba(242, 240, 233, 0.06)' : 'rgba(10, 10, 9, 0.06)';

      ctx.strokeStyle = markColor;
      ctx.lineWidth = 1;

      const size = 3;
      for (const p of points) {
        ctx.beginPath();
        ctx.moveTo(p.x - size, p.y);
        ctx.lineTo(p.x + size, p.y);
        ctx.moveTo(p.x, p.y - size);
        ctx.lineTo(p.x, p.y + size);
        ctx.stroke();
      }
    };

    const render = () => {
      if (document.hidden) {
        animationFrameId = requestAnimationFrame(render);
        return;
      }

      // If mouse is idle for > 40 frames, throttle rendering
      if (!isMoving) {
        idleTicks++;
        if (idleTicks > 60) {
          animationFrameId = requestAnimationFrame(render);
          return;
        }
      }

      ctx.clearRect(0, 0, width, height);
      const isInk = document.documentElement.dataset.theme === 'ink';
      // Subtle opacity shift if approvals are pending (quiet vertical emphasis)
      const baseAlpha = hasPendingApprovals ? 0.08 : 0.05;
      const markColor = isInk
        ? `rgba(242, 240, 233, ${baseAlpha})`
        : `rgba(10, 10, 9, ${baseAlpha})`;

      ctx.strokeStyle = markColor;
      ctx.lineWidth = 1;

      const size = 3;
      for (const p of points) {
        const dx = mouseX - p.x;
        const dy = mouseY - p.y;
        const dist = Math.hypot(dx, dy);

        let targetX = p.x;
        let targetY = p.y;

        // Micro-displacement within 140px radius: max 3px travel
        if (dist < 140) {
          const force = (1 - dist / 140) * 3;
          targetX = p.x - (dx / (dist || 1)) * force;
          targetY = p.y - (dy / (dist || 1)) * force;
        }

        // Spring ease towards target
        p.curX += (targetX - p.curX) * 0.12;
        p.curY += (targetY - p.curY) * 0.12;

        ctx.beginPath();
        ctx.moveTo(p.curX - size, p.curY);
        ctx.lineTo(p.curX + size, p.curY);
        ctx.moveTo(p.curX, p.curY - size);
        ctx.lineTo(p.curX, p.curY + size);
        ctx.stroke();
      }

      animationFrameId = requestAnimationFrame(render);
    };

    window.addEventListener('resize', resize, { passive: true });
    window.addEventListener('pointermove', handlePointerMove, { passive: true });
    window.addEventListener('pointerleave', handlePointerLeave, { passive: true });

    resize();

    if (!prefersReducedMotion) {
      animationFrameId = requestAnimationFrame(render);
    }

    return () => {
      window.removeEventListener('resize', resize);
      window.removeEventListener('pointermove', handlePointerMove);
      window.removeEventListener('pointerleave', handlePointerLeave);
      if (animationFrameId !== null) {
        cancelAnimationFrame(animationFrameId);
      }
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
