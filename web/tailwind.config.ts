import type { Config } from 'tailwindcss';

export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        jv: {
          bg: 'var(--jv-bg)',
          surface: 'var(--jv-surface)',
          sunken: 'var(--jv-surface-sunken)',
          ink: 'var(--jv-ink)',
          'ink-soft': 'var(--jv-ink-soft)',
          muted: 'var(--jv-muted)',
          rule: 'var(--jv-rule)',
          'rule-strong': 'var(--jv-rule-strong)',
          'inverse-bg': 'var(--jv-inverse-bg)',
          'inverse-fg': 'var(--jv-inverse-fg)',
        },
        // Backwards-compatible aliases dynamically mapped to current theme
        canvas: 'var(--jv-bg)',
        ink: 'var(--jv-surface)',
        paper: 'var(--jv-ink)',
        pure: 'var(--jv-ink)',
        grey: {
          100: 'var(--jv-ink)',
          300: 'var(--jv-ink-soft)',
          500: 'var(--jv-muted)',
          700: 'var(--jv-rule)',
        },
      },
      fontFamily: {
        display: ['"Instrument Serif"', 'Georgia', 'serif'],
        interface: ['Geist', '-apple-system', 'BlinkMacSystemFont', 'sans-serif'],
        machine: ['"Geist Mono"', 'ui-monospace', 'monospace'],
      },
      scale: {
        tactile: '0.97',
      },
      letterSpacing: {
        editorial: '-0.02em',
        widest: '0.15em',
      },
      transitionDuration: {
        instant: '80ms',
        fast: '140ms',
        base: '220ms',
        page: '420ms',
      },
      transitionTimingFunction: {
        editorial: 'cubic-bezier(0.16, 1, 0.3, 1)',
      },
    },
  },
  plugins: [],
} satisfies Config;
