/**
 * A tracked skeleton in the editor.
 *
 * A skeleton's geometry is its joints (`elements`); its `points` are empty on the server.
 * Until iteration 68 the editor's track path knew only `points`: a tracked skeleton was
 * drawn on no frame, its joints did not move between keyframes, and saving any edit to the
 * track sent its keyframes without joints — which replaces them, deleting every one.
 */

import { describe, expect, it } from 'vitest';

import type { AnnotationDocument, ApiSkeletonElement, ApiTrack } from '@/api/types';
import { frameAnnotations, toApiTrackUpdate } from '../adapters';
import { interpolateElements, interpolateTrack } from '../interpolate';
import { markDeparture, placeKeyframe, toggleKeyframe } from '../keyframes';

function joint(label: string, x: number, y: number, outside = false): ApiSkeletonElement {
  return { label_id: label, points: [x, y], occluded: false, outside };
}

function keyframe(frame: number, elements: ApiSkeletonElement[]) {
  return {
    frame,
    points: [],
    rotation: 0,
    occluded: false,
    outside: false,
    keyframe: true,
    z_order: 0,
    attributes: {},
    elements,
  };
}

/** Nose moving right, tail moving down, between frames 0 and 10. */
function person(): ApiTrack {
  return {
    id: 'track-1',
    client_id: null,
    label_id: 'person',
    shape_type: 'skeleton',
    group: null,
    object_id: null,
    source: 'manual',
    confidence: null,
    attributes: {},
    shapes: [
      keyframe(0, [joint('nose', 0, 0), joint('tail', 10, 10)]),
      keyframe(10, [joint('nose', 100, 0), joint('tail', 10, 110)]),
    ],
  };
}

function document(track: ApiTrack): AnnotationDocument {
  return { job_id: 'job', annotation_version: 1, shapes: [], tracks: [track], tags: [] };
}

describe('interpolating a skeleton', () => {
  it('moves each joint on its own, matched by label — the server`s rule', () => {
    // The same vectors as `TestTrackedSkeleton` in server/tests/unit/test_interpolation.py.
    const reordered = interpolateElements(
      [joint('a', 0, 0), joint('b', 10, 10)],
      [joint('b', 10, 110), joint('a', 100, 0)],
      0.5,
    );
    expect(reordered.map((j) => [j.label_id, j.points])).toEqual([
      ['a', [50, 0]],
      ['b', [10, 60]],
    ]);

    const leaving = interpolateElements(
      [joint('a', 0, 0), joint('b', 10, 10, true)],
      [joint('a', 100, 0, true), joint('b', 50, 50)],
      0.5,
    );
    expect(leaving[0]!.points).toEqual([0, 0]);
    expect(leaving[1]!.outside).toBe(true);
  });

  it('gives a track its joints between keyframes, and leaves the keyframes alone', () => {
    const track = person();
    const keyframes = track.shapes.map((shape) => ({ ...shape }));
    const middle = interpolateTrack(keyframes, 'skeleton', 5);
    expect(middle?.elements?.map((j) => j.points)).toEqual([
      [50, 0],
      [10, 60],
    ]);
    expect(track.shapes[0]!.elements![0]!.points).toEqual([0, 0]);
  });
});

describe('drawing a tracked skeleton', () => {
  it('is on the canvas between keyframes, with its joints and points to pick it by', () => {
    const [drawn] = frameAnnotations(document(person()), 5);
    expect(drawn?.shapeType).toBe('skeleton');
    expect(drawn?.elements?.map((j) => [j.labelId, j.points])).toEqual([
      ['nose', [50, 0]],
      ['tail', [10, 60]],
    ]);
    expect(drawn?.points).toEqual([50, 0, 10, 60]);
  });
});

describe('editing a tracked skeleton', () => {
  it('keeps every keyframe`s joints when the track is saved', () => {
    const update = toApiTrackUpdate(person()) as { shapes: { elements?: unknown }[] };
    expect(update.shapes.map((shape) => shape.elements)).toEqual([
      [joint('nose', 0, 0), joint('tail', 10, 10)],
      [joint('nose', 100, 0), joint('tail', 10, 110)],
    ]);
  });

  it('adds a keyframe carrying the joints shown there', () => {
    const result = toggleKeyframe(person(), 5);
    if (!result.ok || !result.track) throw new Error('expected a track');
    const added = result.track.shapes.find((shape) => shape.frame === 5);
    expect(added?.elements?.map((j) => j.points)).toEqual([
      [50, 0],
      [10, 60],
    ]);
  });

  it('records joints dragged on a frame as a keyframe there', () => {
    const moved = placeKeyframe(person(), 5, {
      points: [60, 0, 10, 60],
      rotation: 0,
      occluded: false,
      elements: [joint('nose', 60, 0), joint('tail', 10, 60)],
    });
    expect(moved?.shapes.find((shape) => shape.frame === 5)?.elements?.[0]!.points).toEqual([
      60, 0,
    ]);
  });

  it('carries the last joints onto a departure', () => {
    const result = markDeparture(person(), 12);
    if (!result.ok || !result.track) throw new Error('expected a track');
    const departure = result.track.shapes.find((shape) => shape.frame === 12);
    expect(departure?.outside).toBe(true);
    expect(departure?.elements?.length).toBe(2);
  });
});
