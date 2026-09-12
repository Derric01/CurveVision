/**
 * Where the timeline says a track is.
 *
 * The load-bearing test in this file is `agrees with the interpolator on every frame`. The
 * timeline derives presence from the keyframes in one pass; `interpolateTrack` decides it
 * per frame while producing a shape. Two implementations of the same rule will drift, and
 * the drift is invisible: the bar would say the object is there and the canvas would draw
 * nothing, or worse, the reverse. So the property is asserted directly, over every frame of
 * every fixture, rather than spot-checked.
 */

import { describe, expect, it } from 'vitest';
import {
  adjacentKeyframe,
  framePosition,
  interpolateTrack,
  isPresent,
  trackRows,
  trackSegments,
  type Keyframe,
} from '../timeline';
import type { AnnotationDocument, ApiTrack } from '@/api/types';

function keyframe(frame: number, outside = false): Keyframe {
  return { frame, points: [frame, frame, frame + 10, frame + 10], rotation: 0, occluded: false, outside, keyframe: true };
}

/** The shapes of track lifetime that actually occur, named. */
const FIXTURES: Record<string, Keyframe[]> = {
  'a single keyframe, never leaving': [keyframe(0)],
  'two keyframes, never leaving': [keyframe(0), keyframe(10)],
  'starts late': [keyframe(5), keyframe(9)],
  'leaves and never returns': [keyframe(0), keyframe(4, true)],
  'leaves and comes back': [keyframe(0), keyframe(3, true), keyframe(7), keyframe(12)],
  'leaves twice': [
    keyframe(0),
    keyframe(2, true),
    keyframe(5),
    keyframe(8, true),
    keyframe(11),
  ],
  'outside from the very first keyframe': [keyframe(0, true), keyframe(6)],
  'adjacent departure and return': [keyframe(0), keyframe(3, true), keyframe(4)],
};

const START = 0;
const STOP = 15;

describe('trackSegments', () => {
  it('agrees with the interpolator on every frame', () => {
    for (const [name, keyframes] of Object.entries(FIXTURES)) {
      const segments = trackSegments(keyframes, START, STOP);
      for (let frame = START; frame <= STOP; frame++) {
        const interpolated = interpolateTrack(keyframes, 'rectangle', frame) !== null;
        expect(isPresent(segments, frame), `${name} @ frame ${frame}`).toBe(interpolated);
      }
    }
  });

  it('holds to the end of the job past the last keyframe', () => {
    // Not a quirk: `interpolateTrack` holds the last position rather than vanishing, so a
    // track annotated for ten frames of a hundred-frame job really is present throughout.
    expect(trackSegments([keyframe(0), keyframe(4)], 0, 99)).toEqual([{ start: 0, stop: 99 }]);
  });

  it('ends a run the frame before the departure, not on it', () => {
    // The object is gone *at* the outside keyframe. Ending the bar on that frame would
    // claim it is still there.
    expect(trackSegments([keyframe(0), keyframe(4, true)], 0, 10)).toEqual([
      { start: 0, stop: 3 },
    ]);
  });

  it('produces one run per appearance', () => {
    expect(trackSegments(FIXTURES['leaves twice']!, 0, 15)).toEqual([
      { start: 0, stop: 1 },
      { start: 5, stop: 7 },
      { start: 11, stop: 15 },
    ]);
  });

  it('drops a run that has no frames in it', () => {
    // A departure on the frame right after an arrival leaves nothing to draw.
    expect(trackSegments([keyframe(3), keyframe(3, true)], 0, 10)).toEqual([]);
  });

  it('clips to the job rather than reporting frames outside it', () => {
    // A job is a slice of a task, so a track can start before this job's first frame.
    expect(trackSegments([keyframe(0), keyframe(20)], 5, 12)).toEqual([{ start: 5, stop: 12 }]);
  });

  it('is empty for a track with no keyframes at all', () => {
    expect(trackSegments([], 0, 10)).toEqual([]);
  });

  it('is empty for an inverted range rather than throwing', () => {
    expect(trackSegments([keyframe(0)], 10, 0)).toEqual([]);
  });

  it('does not care what order the keyframes arrive in', () => {
    const scrambled = [keyframe(7), keyframe(0), keyframe(3, true)];
    expect(trackSegments([...scrambled].sort((a, b) => a.frame - b.frame), 0, 10)).toEqual([
      { start: 0, stop: 2 },
      { start: 7, stop: 10 },
    ]);
  });
});

// ------------------------------------------------------------------------------- rows

function documentWith(tracks: Partial<ApiTrack>[]): AnnotationDocument {
  return {
    job_id: 'job-1',
    annotation_version: 1,
    shapes: [],
    tags: [],
    tracks: tracks.map((track, index) => ({
      id: `track-${index}`,
      client_id: null,
      label_id: 'label-1',
      shape_type: 'rectangle',
      group: null,
      object_id: null,
      source: 'manual',
      confidence: null,
      attributes: {},
      shapes: [],
      ...track,
    })) as ApiTrack[],
  };
}

describe('trackRows', () => {
  it('reports keyframes and departures separately', () => {
    const document = documentWith([
      {
        shapes: [
          { frame: 0, points: [0, 0, 1, 1], rotation: 0, occluded: false, outside: false, keyframe: true, z_order: 0, attributes: {} },
          { frame: 4, points: [0, 0, 1, 1], rotation: 0, occluded: false, outside: true, keyframe: true, z_order: 0, attributes: {} },
        ],
      },
    ]);

    const [row] = trackRows(document, 0, 10);
    expect(row?.keyframes).toEqual([0, 4]);
    expect(row?.departures).toEqual([4]);
    expect(row?.segments).toEqual([{ start: 0, stop: 3 }]);
  });

  it('returns nothing for no document, rather than throwing', () => {
    expect(trackRows(undefined, 0, 10)).toEqual([]);
  });

  it('keeps a track whose keyframes all fall outside this job', () => {
    // It has no segments here, but it is still one of the job's tracks; dropping the row
    // would make a track silently vanish from the timeline rather than read as empty.
    const document = documentWith([
      {
        shapes: [
          { frame: 40, points: [0, 0, 1, 1], rotation: 0, occluded: false, outside: false, keyframe: true, z_order: 0, attributes: {} },
        ],
      },
    ]);
    const [row] = trackRows(document, 0, 10);
    expect(row?.segments).toEqual([]);
    expect(row?.keyframes).toEqual([40]);
  });
});

// -------------------------------------------------------------------------- positions

describe('framePosition', () => {
  it('maps the range onto 0..1', () => {
    expect(framePosition(0, 0, 10)).toBe(0);
    expect(framePosition(5, 0, 10)).toBe(0.5);
    expect(framePosition(10, 0, 10)).toBe(1);
  });

  it('is 0 for a one-frame job rather than dividing by zero', () => {
    expect(framePosition(7, 7, 7)).toBe(0);
  });

  it('clamps rather than positioning a marker off the bar', () => {
    expect(framePosition(-5, 0, 10)).toBe(0);
    expect(framePosition(99, 0, 10)).toBe(1);
  });
});

describe('adjacentKeyframe', () => {
  const keyframes = [0, 4, 9, 15];

  it('finds the next one', () => {
    expect(adjacentKeyframe(keyframes, 0, 1)).toBe(4);
    expect(adjacentKeyframe(keyframes, 5, 1)).toBe(9);
  });

  it('finds the previous one', () => {
    expect(adjacentKeyframe(keyframes, 9, -1)).toBe(4);
    expect(adjacentKeyframe(keyframes, 5, -1)).toBe(4);
  });

  it('never returns the frame it is already on', () => {
    // Otherwise "jump to next keyframe" would not move.
    expect(adjacentKeyframe(keyframes, 4, 1)).toBe(9);
    expect(adjacentKeyframe(keyframes, 4, -1)).toBe(0);
  });

  it('is null at either end', () => {
    expect(adjacentKeyframe(keyframes, 15, 1)).toBeNull();
    expect(adjacentKeyframe(keyframes, 0, -1)).toBeNull();
    expect(adjacentKeyframe([], 3, 1)).toBeNull();
  });
});
