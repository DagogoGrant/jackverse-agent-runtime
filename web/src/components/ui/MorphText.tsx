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
      setIsTransitioning(true);
      const timer = setTimeout(() => {
        setCurrentText(text);
        setIsTransitioning(false);
      }, 120);
      return () => clearTimeout(timer);
    }
  }, [text, currentText]);

  return (
    <Component
      className={`inline-block transition-all duration-150 ${
        isTransitioning
          ? 'opacity-0 translate-y-1'
          : 'opacity-100 translate-y-0'
      } ${className}`}
    >
      {currentText}
    </Component>
  );
};
