/** @type {import('tailwindcss').Config} */

// Every colour is a CSS variable (`src/index.css`), so one attribute on <html> switches the
// whole application between its two themes: pitch black, and white. The names stay `ink`
// (the neutral scale, 950 the page and 100 the text) and `curve` (the accent), so a theme
// is a change of values, never of markup.
const scale = (name, steps) =>
  Object.fromEntries(steps.map((step) => [step, `rgb(var(--${name}-${step}) / <alpha-value>)`]));

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: scale('ink', [950, 900, 850, 800, 700, 600, 500, 400, 300, 200, 100]),
        curve: scale('curve', [950, 800, 600, 500, 400, 300, 200]),
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
    },
  },
  plugins: [],
};
