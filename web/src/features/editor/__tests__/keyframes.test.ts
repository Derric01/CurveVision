/**
 * Editing a track's keyframes.
 *
 * The claim these operations rest on is that **adding or removing a keyframe does not move
 * the object anywhere else**. It is easy to write an implementation that looks right and
 * quietly shifts every frame between the new keyframe and its neighbours — the annotator
 * sees a box they did not draw, on frames they were not looking at, and finds out much
 * later.
 *
 * So the tests do not check the keyframe list and stop. `framesThatMoved` compares the
 * *displayed shape on every frame* before and after, and the central tests assert that the
 * set of frames which moved is exactly the set that was allowed to.
 */

import { describe, expect, it } from 'vitest';

import type { ApiTrack } from '@/api/types';
import {
  framesThatMoved,
  keyframeAt,
  markDeparture,
  moveKeyframe,
  positionAt,
  removeKeyframe,
  toggleKeyframe,
} from '../keyframes';

function shape(frame: number, x: number, outside = false) {
  return {
    frame,
    points: [x, 0, x + 20, 20],
    rotation: 0,
    occluded: false,
    outside,
    keyframe: true,
    attributes: {},
  };
}

/** A car crossing the frame: x = 0 at frame 0, x = 100 at frame 10. */
function movingTrack(shapes = [shape(0, 0), shape(10, 100)]): ApiTrack {
  return {
    id: 'track-1',
    client_id: null,
    label_id: 'label-1',
    shape_type: 'rectangle',
    group: null,
    object_id: null,
    source: 'manual',
    confidence: null,
    attributes: {},
    shapes,
  } as ApiTrack;
}

function frames(track: ApiTrack): number[] {
  return track.shapes.map((s) => s.frame);
}

function ok(result: ReturnType<typeof toggleKeyframe>): ApiTrack {
  if (!result.ok || !result.track) throw new Error(`expected an edited track, got ${JSON.stringify(result)}`);
  return result.track;
}

describe('positionAt', () => {
  it('interpolates between keyframes', () => {
    const position = positionAt(movingTrack(), 5);
    expect(position?.points).toEqual([50, 0, 70, 20]);
  });

  it('is null where the object is not on screen', () => {
    const track = movingTrack([shape(0, 0), shape(5, 50, true), shape(10, 100)]);
    expect(positionAt(track, 7)).toBeNull();
  });
});

describe('toggleKeyframe — adding', () => {
  it('records the interpolated position, so the box does not jump', () => {
    const before = movingTrack();
    const after = ok(toggleKeyframe(before, 5));

    expect(keyframeAt(after, 5)?.points).toEqual([50, 0, 70, 20]);
  });

  it('moves the object on no frame at all', () => {
    // The load-bearing assertion. An implementation that copied the previous keyframe's
    // points would snap the box backwards on frames 1-9 and pass every other test here.
    const before = movingTrack();
    const after = ok(toggleKeyframe(before, 5));

    expect(framesThatMoved(before, after, 0, 10)).toEqual([]);
  });

  it('moves nothing wherever the keyframe is added', () => {
    const before = movingTrack();
    for (const frame of [1, 2, 3, 4, 6, 7, 8, 9]) {
      const after = ok(toggleKeyframe(before, frame));
      expect(framesThatMoved(before, after, 0, 10)).toEqual([]);
    }
  });

  it('adds the frame to the keyframe list', () => {
    expect(frames(ok(toggleKeyframe(movingTrack(), 5)))).toEqual([0, 5, 10]);
  });

  it('refuses where the object is not on screen', () => {
    const track = movingTrack([shape(0, 0), shape(5, 50, true), shape(10, 100)]);
    const result = toggleKeyframe(track, 7);

    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.refusal.reason).toMatch(/not on screen/);
  });
});

describe('toggleKeyframe — removing', () => {
  it('removes a keyframe that is already there', () => {
    const track = movingTrack([shape(0, 0), shape(5, 50), shape(10, 100)]);
    expect(frames(ok(toggleKeyframe(track, 5)))).toEqual([0, 10]);
  });

  it('changes the shape only between the removed keyframe’s neighbours', () => {
    // Removing a keyframe is *supposed* to change the interpolation — that is what it means.
    // What it must not do is touch frames outside the span it governed.
    const before = movingTrack([shape(0, 0), shape(5, 90), shape(10, 100)]);
    const after = ok(toggleKeyframe(before, 5));

    const moved = framesThatMoved(before, after, 0, 10);
    expect(moved).not.toContain(0);
    expect(moved).not.toContain(10);
    expect(moved).toContain(5);
  });

  it('reports that the track should be deleted when the last keyframe goes', () => {
    // A track with no shapes is not something the API accepts, and an empty one would leave
    // a timeline row for an object that exists nowhere.
    const result = toggleKeyframe(movingTrack([shape(3, 30)]), 3);

    expect(result).toEqual({ ok: true, track: null, deleteTrack: true });
  });
});

describe('removeKeyframe', () => {
  it('says so when there is nothing to remove', () => {
    const result = removeKeyframe(movingTrack(), 4);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.refusal.reason).toMatch(/no keyframe/);
  });
});

describe('markDeparture', () => {
  it('ends the track’s presence at that frame', () => {
    const before = movingTrack();
    const after = ok(markDeparture(before, 6));

    expect(keyframeAt(after, 6)?.outside).toBe(true);
    expect(positionAt(after, 7)).toBeNull();
    expect(positionAt(after, 5)).not.toBeNull();
  });

  it('carries the position the object was last at, not an empty shape', () => {
    // An exporter materialising this track reads these points; zeroes would export a box
    // at the origin.
    const after = ok(markDeparture(movingTrack(), 6));
    expect(keyframeAt(after, 6)?.points).toEqual([60, 0, 80, 20]);
  });

  it('leaves earlier frames exactly where they were', () => {
    const before = movingTrack();
    const after = ok(markDeparture(before, 6));

    expect(framesThatMoved(before, after, 0, 5)).toEqual([]);
  });

  it('converts an existing keyframe rather than adding a second on that frame', () => {
    const before = movingTrack([shape(0, 0), shape(5, 50), shape(10, 100)]);
    const after = ok(markDeparture(before, 5));

    expect(after.shapes.filter((s) => s.frame === 5)).toHaveLength(1);
    expect(keyframeAt(after, 5)?.outside).toBe(true);
  });

  it('pins the frame before, because a departure freezes what leads up to it', () => {
    // `interpolateTrack` holds the previous position when the next keyframe is a departure,
    // rather than animating towards coordinates that are usually stale. Without the pin,
    // dropping a departure at frame 6 would freeze frames 1-5 at the frame-0 position —
    // five frames nobody touched, silently stopped moving.
    const before = movingTrack();
    const after = ok(markDeparture(before, 6));

    expect(keyframeAt(after, 5)).toBeDefined();
    expect(keyframeAt(after, 5)?.outside).toBe(false);
    expect(keyframeAt(after, 5)?.points).toEqual([50, 0, 70, 20]);
  });

  it('does not pin when the frame before already has a keyframe', () => {
    const before = movingTrack([shape(0, 0), shape(5, 50), shape(10, 100)]);
    const after = ok(markDeparture(before, 6));

    expect(frames(after)).toEqual([0, 5, 6, 10]);
  });

  it('leaves earlier frames untouched even when it converts an existing keyframe', () => {
    const before = movingTrack([shape(0, 0), shape(5, 50), shape(10, 100)]);
    const after = ok(markDeparture(before, 5));

    expect(framesThatMoved(before, after, 0, 4)).toEqual([]);
  });

  it('refuses to mark the same departure twice', () => {
    const track = movingTrack([shape(0, 0), shape(5, 50, true)]);
    const result = markDeparture(track, 5);

    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.refusal.reason).toMatch(/already leaves/);
  });

  it('refuses when it would be the track’s only keyframe', () => {
    const result = markDeparture(movingTrack([shape(4, 40)]), 4);

    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.refusal.reason).toMatch(/never on screen/);
  });
});

describe('moveKeyframe', () => {
  it('keeps the geometry and changes only the frame', () => {
    const before = movingTrack([shape(0, 0), shape(5, 50), shape(10, 100)]);
    const after = ok(moveKeyframe(before, 5, 7));

    expect(frames(after)).toEqual([0, 7, 10]);
    expect(keyframeAt(after, 7)?.points).toEqual([50, 0, 70, 20]);
  });

  it('refuses to land on an occupied frame', () => {
    // The API rejects two shapes on one frame, and merging them would silently discard one
    // of the annotator's positions.
    const before = movingTrack([shape(0, 0), shape(5, 50), shape(10, 100)]);
    const result = moveKeyframe(before, 5, 10);

    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.refusal.reason).toMatch(/already has a keyframe/);
  });

  it('refuses to move before frame 0', () => {
    const result = moveKeyframe(movingTrack(), 10, -1);
    expect(result.ok).toBe(false);
  });

  it('is a no-op when the destination is where it already is', () => {
    const before = movingTrack();
    const result = moveKeyframe(before, 10, 10);
    expect(result).toEqual({ ok: true, track: before });
  });

  it('says so when there is no keyframe to move', () => {
    const result = moveKeyframe(movingTrack(), 4, 6);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.refusal.reason).toMatch(/no keyframe/);
  });
});

describe('framesThatMoved', () => {
  it('is empty for a track compared with itself', () => {
    const track = movingTrack();
    expect(framesThatMoved(track, track, 0, 10)).toEqual([]);
  });

  it('catches a shifted shape', () => {
    const before = movingTrack();
    const after = movingTrack([shape(0, 5), shape(10, 100)]);
    expect(framesThatMoved(before, after, 0, 10)).toContain(0);
  });

  it('counts a frame where presence itself changed', () => {
    const before = movingTrack();
    const after = movingTrack([shape(0, 0), shape(6, 60, true), shape(10, 100)]);
    expect(framesThatMoved(before, after, 0, 10)).toContain(7);
  });
});
