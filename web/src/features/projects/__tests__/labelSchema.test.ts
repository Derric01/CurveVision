/**
 * Adding to a project's label schema: the name that is actually sent, and the colour
 * proposed for it.
 *
 * What is not tested here, because it is not here: duplicate names and permission. Both
 * are the server's answers, and a second copy of either rule in the browser would be a
 * second thing to keep true.
 */

import { describe, expect, it } from 'vitest';
import {
  canAddLabel,
  describeLabelUsage,
  nextLabelColor,
  normaliseLabelName,
} from '../labelSchema';

describe('normaliseLabelName', () => {
  it('trims, because the server compares names as strings', () => {
    expect(normaliseLabelName('  van  ')).toBe('van');
  });

  it('collapses internal whitespace, so one class cannot become two', () => {
    // "school  bus" and "school bus" are the same class to everybody except a string
    // comparison, and a string comparison is what decides whether this is a duplicate.
    expect(normaliseLabelName('school  bus')).toBe('school bus');
    expect(normaliseLabelName('school\tbus')).toBe('school bus');
  });

  it('leaves an ordinary name alone', () => {
    expect(normaliseLabelName('delivery van')).toBe('delivery van');
  });
});

describe('canAddLabel', () => {
  it('needs a name', () => {
    expect(canAddLabel('van')).toBe(true);
    expect(canAddLabel('')).toBe(false);
  });

  it('does not count whitespace as one', () => {
    expect(canAddLabel('   ')).toBe(false);
    expect(canAddLabel('\n\t')).toBe(false);
  });
});

describe('nextLabelColor', () => {
  it('proposes the first palette colour for an empty schema', () => {
    expect(nextLabelColor([])).toBe('#ef4444');
  });

  it('skips the colours already in use, so a schema stays readable on the canvas', () => {
    expect(nextLabelColor([{ color: '#ef4444' }, { color: '#f97316' }])).toBe('#eab308');
  });

  it('compares case-insensitively, because hex is written both ways', () => {
    expect(nextLabelColor([{ color: '#EF4444' }])).toBe('#f97316');
  });

  it('ignores a colour that is not in the palette rather than being thrown by it', () => {
    // The server's own default is `#38bdf8`, and a person can pick anything at all.
    expect(nextLabelColor([{ color: '#123456' }])).toBe('#ef4444');
  });

  it('cycles rather than returning nothing once every colour is taken', () => {
    const everything = [
      '#ef4444', '#f97316', '#eab308', '#22c55e', '#14b8a6',
      '#38bdf8', '#6366f1', '#a855f7', '#ec4899',
    ].map((color) => ({ color }));
    expect(nextLabelColor(everything)).toBe('#ef4444');
    expect(nextLabelColor([...everything, { color: '#ef4444' }])).toBe('#f97316');
  });
});

describe('describeLabelUsage', () => {
  it('says nothing about a label nothing uses', () => {
    expect(describeLabelUsage('van', { car: 12 })).toBeNull();
    expect(describeLabelUsage('van', {})).toBeNull();
  });

  it('counts, and pluralises', () => {
    expect(describeLabelUsage('car', { car: 1 })).toBe('1 annotation uses this label');
    expect(describeLabelUsage('car', { car: 12 })).toBe('12 annotations use this label');
  });
});
