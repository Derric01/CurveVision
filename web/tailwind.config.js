/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // CurveVision's own palette: a cool slate ground with a curve-teal accent, so the
        // product reads as itself rather than as a default component library.
        ink: {
          950: '#080b12',
          900: '#0d1117',
          850: '#121821',
          800: '#182130',
          700: '#232f42',
          600: '#33425a',
          500: '#4a5c78',
          400: '#6c7f9c',
          300: '#94a5bf',
          200: '#c2cddd',
          100: '#e4eaf3',
        },
        curve: {
          600: '#0d9488',
          500: '#14b8a6',
          400: '#2dd4bf',
          300: '#5eead4',
        },
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
    },
  },
  plugins: [],
};
