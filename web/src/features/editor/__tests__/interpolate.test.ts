/**
 * Client-side interpolation.
 *
 * These cases mirror `server/tests/unit/test_interpolation.py` one for one. The client
 * interpolates locally so scrubbing a video does not cost a round trip per frame, which
 * means two implementations of the same algorithm exist — and the only defensible way to
 * keep them honest is to hold both to the same vectors.
 */

import { describe, expect, it } from 'vitest';
import { interpolatePoints, interpolateTrack, lerpAngle, resample, type Keyframe } from '../interpolate';

function kf(frame: number, points: number[], extra: Partial<Keyframe> = {}): Keyframe {
  return {
    frame,
    points,
    rotation: 0,
    occluded: false,
    outside: false,
    keyframe: true,
    ...extra,
  };
}

describe('interpolatePoints', () => {
  it('finds the midpoint of a rectangle', () => {
    expect(interpolatePoints([0, 0, 10, 10], [10, 10, 20, 20], 0.5, 'rectangle')).toEqual([
      5, 5, 15, 15,
    ]);
  });

  it('returns the endpoints exactly', () => {
    const start = [0, 0, 4, 4];
    const end = [8, 8, 12, 12];
    expect(interpolatePoints(start, end, 0, 'rectangle')).toEqual(start);
    expect(interpolatePoints(start, end, 1, 'rectangle')).toEqual(end);
  });

  it('pairs equal-length polygons positionally', () => {
    expect(
      interpolatePoints([0, 0, 10, 0, 10, 10], [10, 10, 20, 10, 20, 20], 0.5, 'polygon'),
    ).toEqual([5, 5, 15, 5, 15, 15]);
  });

  it('resamples polygons whose vertex counts differ', () => {
    // Pairing by index would blend vertex 3 of a triangle towards a square's vertex 3,
    // which means something entirely different — the shape visibly scrambles.
    const triangle = [0, 0, 10, 0, 5, 10];
    const square = [0, 0, 10, 0, 10, 10, 0, 10];

    const blended = interpolatePoints(triangle, square, 0.5, 'polygon');

    expect(blended).toHaveLength(square.length);
    const xs = blended.filter((_, i) => i % 2 === 0);
    const ys = blended.filter((_, i) => i % 2 === 1);
    expect(Math.min(...xs)).toBeGreaterThanOrEqual(-0.001);
    expect(Math.max(...xs)).toBeLessThanOrEqual(10.001);
    expect(Math.min(...ys)).toBeGreaterThanOrEqual(-0.001);
    expect(Math.max(...ys)).toBeLessThanOrEqual(10.001);
  });

  it('holds the start when arity cannot be reconciled', () => {
    expect(interpolatePoints([0, 0, 1, 1], [0, 0], 0.5, 'rectangle')).toEqual([0, 0, 1, 1]);
  });
});

describe('resample', () => {
  it('keeps both endpoints of an open path', () => {
    const result = resample([0, 0, 10, 0], 3, false);
    expect(result.slice(0, 2)).toEqual([0, 0]);
    expect(result.slice(-2)).toEqual([10, 0]);
    expect(result).toHaveLength(6);
  });

  it('does not repeat the first vertex of a closed path', () => {
    const result = resample([0, 0, 10, 0, 10, 10, 0, 10], 4, true);
    expect(result).toHaveLength(8);
    expect(result.slice(-2)).not.toEqual(result.slice(0, 2));
  });

  it('survives a zero-length path', () => {
    expect(resample([2, 2, 2, 2], 4, false)).toEqual([2, 2, 2, 2, 2, 2, 2, 2]);
  });
});

describe('lerpAngle', () => {
  it('takes the short way round through zero', () => {
    expect(lerpAngle(350, 10, 0.5)).toBeCloseTo(360);
  });

  it('interpolates normally inside one turn', () => {
    expect(lerpAngle(0, 90, 0.5)).toBeCloseTo(45);
  });
});

describe('interpolateTrack', () => {
  it('returns a keyframe unchanged', () => {
    const result = interpolateTrack([kf(0, [0, 0, 10, 10]), kf(10, [100, 100, 110, 110])], 'rectangle', 0);
    expect(result?.points).toEqual([0, 0, 10, 10]);
    expect(result?.keyframe).toBe(true);
  });

  it('marks an interpolated frame as not a keyframe', () => {
    const result = interpolateTrack(
      [kf(0, [0, 0, 10, 10]), kf(10, [100, 100, 110, 110])],
      'rectangle',
      5,
    );
    expect(result?.points).toEqual([50, 50, 60, 60]);
    expect(result?.keyframe).toBe(false);
  });

  it('says nothing exists before the first keyframe', () => {
    expect(interpolateTrack([kf(5, [0, 0, 10, 10])], 'rectangle', 4)).toBeNull();
  });

  it('holds the last position after the final keyframe', () => {
    const result = interpolateTrack([kf(0, [0, 0, 10, 10]), kf(5, [50, 50, 60, 60])], 'rectangle', 99);
    expect(result?.points).toEqual([50, 50, 60, 60]);
  });

  it('removes the object across an outside keyframe', () => {
    const keyframes = [
      kf(0, [0, 0, 10, 10]),
      kf(5, [50, 50, 60, 60], { outside: true }),
      kf(10, [90, 90, 100, 100]),
    ];
    expect(interpolateTrack(keyframes, 'rectangle', 5)).toBeNull();
    expect(interpolateTrack(keyframes, 'rectangle', 7)).toBeNull();
    expect(interpolateTrack(keyframes, 'rectangle', 10)).not.toBeNull();
  });

  it('holds position rather than drifting towards an outside keyframe', () => {
    const keyframes = [kf(0, [0, 0, 10, 10]), kf(10, [500, 500, 510, 510], { outside: true })];
    expect(interpolateTrack(keyframes, 'rectangle', 5)?.points).toEqual([0, 0, 10, 10]);
  });

  it('carries occlusion forward', () => {
    const keyframes = [
      kf(0, [0, 0, 10, 10], { occluded: true }),
      kf(10, [100, 100, 110, 110]),
    ];
    expect(interpolateTrack(keyframes, 'rectangle', 5)?.occluded).toBe(true);
  });

  it('handles unsorted keyframes', () => {
    // The API returns them ordered, but a locally-edited track may not be.
    const keyframes = [kf(10, [100, 100, 110, 110]), kf(0, [0, 0, 10, 10])];
    expect(interpolateTrack(keyframes, 'rectangle', 5)?.points).toEqual([50, 50, 60, 60]);
  });

  it('returns nothing for an empty track', () => {
    expect(interpolateTrack([], 'rectangle', 0)).toBeNull();
  });
});
