import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, renderHook, act } from '@testing-library/react';
import { useTheme } from '../../src/hooks/useTheme';
import { ThemeSwitch } from '../../src/components/ui/ThemeSwitch';

describe('JackVerse Dual Theme Architecture (Paper & Ink)', () => {
  beforeEach(() => {
    localStorage.clear();
    delete document.documentElement.dataset.theme;
    document.documentElement.className = '';
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('initializes with Paper as default theme when localStorage is empty', () => {
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe('paper');
    expect(result.current.isPaper).toBe(true);
    expect(result.current.isInk).toBe(false);
    expect(document.documentElement.dataset.theme).toBe('paper');
  });

  it('restores stored Ink theme preference from localStorage on mount', () => {
    localStorage.setItem('jv_theme', 'ink');
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe('ink');
    expect(result.current.isInk).toBe(true);
    expect(document.documentElement.dataset.theme).toBe('ink');
  });

  it('toggles between Paper and Ink mode and persists preference', () => {
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe('paper');

    act(() => {
      result.current.toggleTheme();
    });

    expect(result.current.theme).toBe('ink');
    expect(result.current.isInk).toBe(true);
    expect(localStorage.getItem('jv_theme')).toBe('ink');
    expect(document.documentElement.dataset.theme).toBe('ink');

    act(() => {
      result.current.toggleTheme();
    });

    expect(result.current.theme).toBe('paper');
    expect(result.current.isPaper).toBe(true);
    expect(localStorage.getItem('jv_theme')).toBe('paper');
    expect(document.documentElement.dataset.theme).toBe('paper');
  });

  it('ThemeSwitch renders accessible toggle and responds to user click', () => {
    render(<ThemeSwitch />);

    const switchBtn = screen.getByRole('button', { name: /visual identity/i });
    expect(switchBtn).toBeInTheDocument();
    expect(switchBtn).toHaveAttribute('aria-label', expect.stringContaining('Paper'));

    fireEvent.click(switchBtn);

    expect(document.documentElement.dataset.theme).toBe('ink');
    expect(switchBtn).toHaveAttribute('aria-label', expect.stringContaining('Ink'));
  });

  it('respects prefers-reduced-motion without adding wash class', () => {
    window.matchMedia = vi.fn().mockImplementation((query) => ({
      matches: query === '(prefers-reduced-motion: reduce)',
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));

    const { result } = renderHook(() => useTheme());

    act(() => {
      result.current.setTheme('ink');
    });

    expect(document.documentElement.dataset.theme).toBe('ink');
    expect(document.documentElement.classList.contains('theme-wash')).toBe(false);
  });
});
