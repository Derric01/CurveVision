import { describe, expect, it } from 'vitest';
import { cuboidHint } from '../cuboidHint';

describe('cuboidHint', () => {
  it('explains the two-stage gesture before anything has happened yet', () => {
    const hint = cuboidHint(null);
    expect(hint).toContain('front face');
    expect(hint).toContain('depth');
  });

  it('shows whatever the tool last reported instead of the idle line', () => {
    expect(cuboidHint('Move to set the depth, then click to place the back face')).toBe(
      'Move to set the depth, then click to place the back face',
    );
  });
});
