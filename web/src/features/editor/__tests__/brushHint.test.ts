import { describe, expect, it } from 'vitest';
import { brushHint } from '../brushHint';

describe('brushHint', () => {
  it('explains the tool before anything has happened yet', () => {
    const hint = brushHint(null);
    expect(hint).toContain('Paint a mask');
    expect(hint).toContain('erase');
  });

  // The tool never blocks on a schema decision the way the skeleton tool does, so once a
  // stroke or a resize has something to say, that is the whole line -- no further state to
  // reason about layered on top of it.
  it('shows whatever the tool last reported instead of the idle line', () => {
    expect(brushHint('Editing the selected mask · 14px (] / [) · right-click or Alt to erase')).toBe(
      'Editing the selected mask · 14px (] / [) · right-click or Alt to erase',
    );
  });
});
