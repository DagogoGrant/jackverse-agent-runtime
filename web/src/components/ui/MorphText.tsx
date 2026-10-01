import React, { useState, useEffect } from 'react';

interface MorphTextProps {
  text: string;
  className?: string;
  as?: 'span' | 'div' | 'h1' | 'h2' | 'h3' | 'p';
}

export const MorphText: React.FC<MorphTextProps> = ({
  text,
  className = '',
  as: Component = 'span',
}) => {
  const [currentText, setCurrentText] = useState(text);
  const [isTransitioning, setIsTransitioning] = useState(false);

  useEffect(() => {
    if (text !== currentText) {
      const prefersReducedMotion =
        typeof window !== 'undefined' &&
        window.matchMedia &&
        window.matchMedia('(prefers-reduced-motion: reduce)').matches;

      if (prefersReducedMotion) {
        const timer = setTimeout(() => {
          setCurrentText(text);
        }, 0);
        return () => clearTimeout(timer);
      }

      const timer1 = setTimeout(() => {
        setIsTransitioning(true);
      }, 0);

      const timer2 = setTimeout(() => {
        setCurrentText(text);
        setIsTransitioning(false);
      }, 120);

      return () => {
        clearTimeout(timer1);
        clearTimeout(timer2);
      };
    }
  }, [text, currentText]);

  return (
    <Component
      className={`inline-block transition-[transform,opacity] duration-base ease-editorial ${
        isTransitioning
          ? 'opacity-0 -translate-y-1'
          : 'opacity-100 translate-y-0'
      } ${className}`}
    >
      {currentText}
    </Component>
  );
};
