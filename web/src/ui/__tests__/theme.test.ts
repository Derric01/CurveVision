import { describe, expect, it } from 'vitest';
import { otherTheme, parseTheme } from '../theme';

describe('parseTheme', () => {
  it('is dark unless light was chosen', () => {
    expect(parseTheme(null)).toBe('dark');
    expect(parseTheme(undefined)).toBe('dark');
    expect(parseTheme('dark')).toBe('dark');
    expect(parseTheme('light')).toBe('light');
    expect(parseTheme('sepia')).toBe('dark');
  });
});

describe('otherTheme', () => {
  it('turns one into the other', () => {
    expect(otherTheme('dark')).toBe('light');
    expect(otherTheme('light')).toBe('dark');
  });
});
