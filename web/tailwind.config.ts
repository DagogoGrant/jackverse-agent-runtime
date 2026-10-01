import type { Config } from 'tailwindcss';

export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        canvas: '#050505',
        ink: '#0A0A0A',
        paper: '#F4F3EF',
        pure: '#FFFFFF',
        grey: {
          100: '#E8E7E3',
          300: '#B8B7B2',
          500: '#777672',
          700: '#2D2D2B',
        },
      },
      fontFamily: {
        display: ['"Instrument Serif"', 'Georgia', 'serif'],
        interface: ['Geist', '-apple-system', 'BlinkMacSystemFont', 'sans-serif'],
        machine: ['"Geist Mono"', 'ui-monospace', 'monospace'],
      },
      scale: {
        tactile: '0.96',
      },
      letterSpacing: {
        editorial: '-0.02em',
        widest: '0.15em',
      },
    },
  },
  plugins: [],
} satisfies Config;
