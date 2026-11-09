import { describe, expect, it } from 'vitest';
import { DEFAULT_PLAYBACK_RATE, nextPlaybackFrame, PLAYBACK_RATES } from '../playback';

describe('nextPlaybackFrame', () => {
  it('steps one frame at a time and stops at the last', () => {
    expect(nextPlaybackFrame(0, 3)).toBe(1);
    expect(nextPlaybackFrame(2, 3)).toBe(3);
    expect(nextPlaybackFrame(3, 3)).toBeNull();
  });

  it('respects a job that does not start at frame 0', () => {
    expect(nextPlaybackFrame(500, 999)).toBe(501);
    expect(nextPlaybackFrame(999, 999)).toBeNull();
  });

  it('offers a default speed from its own list', () => {
    expect(PLAYBACK_RATES).toContain(DEFAULT_PLAYBACK_RATE);
  });
});
