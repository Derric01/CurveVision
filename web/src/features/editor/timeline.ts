/**
 * Where each track exists along a job's frame range.
 *
 * A track is not simply "present from its first keyframe to its last". A keyframe marked
 * `outside` is the object *leaving* — it has gone behind a wall, or out of shot — and it may
 * come back later. So a track's presence is a set of intervals, not one span, and drawing it
 * as one span would tell an annotator the object is there when it is not.
 *
 * The rules here are the same ones `interpolateTrack` applies, and
 * `__tests__/timeline.test.ts` asserts that directly: for every frame in the range, "the
 * timeline says present" must equal "interpolation returns a shape". Two implementations of
 * presence that disagree would put the bar in one place and the box in another.
 */

import type { ShapeType } from '@/canvas/types';
import type { AnnotationDocument, ApiTrack } from '@/api/types';
import { interpolateTrack, type Keyframe } from './interpolate';

/** An inclusive run of frames where the track's object is on screen. */
export interface TrackSegment {
  start: number;
  stop: number;
}

export interface TrackRow {
  trackId: string;
  labelId: string;
  shapeType: ShapeType;
  /** Every frame the track stores a shape at, ascending. These are what a user can jump to. */
  keyframes: number[];
  /** The frames at which the object leaves. A subset of `keyframes`. */
  departures: number[];
  segments: TrackSegment[];
}

function toKeyframes(track: ApiTrack): Keyframe[] {
  return track.shapes
    .map((shape) => ({
      frame: shape.frame,
      points: shape.points,
      rotation: shape.rotation,
      occluded: shape.occluded,
      outside: shape.outside,
      keyframe: shape.keyframe,
    }))
    .sort((a, b) => a.frame - b.frame);
}

/**
 * The presence intervals for one track, clipped to `[startFrame, stopFrame]`.
 *
 * Derived from the keyframes directly rather than by probing every frame: a job can be
 * thousands of frames long and this runs on every annotation change.
 */
export function trackSegments(
  keyframes: Keyframe[],
  startFrame: number,
  stopFrame: number,
): TrackSegment[] {
  if (keyframes.length === 0 || stopFrame < startFrame) return [];

  const segments: TrackSegment[] = [];
  let open: number | null = null;

  for (const frame of keyframes) {
    if (frame.outside) {
      // The object leaves *at* this frame, so the run it closes ends the frame before.
      if (open !== null && frame.frame - 1 >= open) segments.push({ start: open, stop: frame.frame - 1 });
      open = null;
    } else if (open === null) {
      open = frame.frame;
    }
  }

  // A track whose last keyframe is not a departure holds its position to the end of the job,
  // which is what `interpolateTrack` does past the final keyframe.
  if (open !== null) segments.push({ start: open, stop: stopFrame });

  return segments
    .map((segment) => ({
      start: Math.max(segment.start, startFrame),
      stop: Math.min(segment.stop, stopFrame),
    }))
    .filter((segment) => segment.stop >= segment.start);
}

/** One row per track in the document, in a stable order. */
export function trackRows(
  document: AnnotationDocument | undefined,
  startFrame: number,
  stopFrame: number,
): TrackRow[] {
  if (!document) return [];

  return document.tracks.map((track) => {
    const keyframes = toKeyframes(track);
    return {
      trackId: track.id,
      labelId: track.label_id,
      shapeType: track.shape_type as ShapeType,
      keyframes: keyframes.map((frame) => frame.frame),
      departures: keyframes.filter((frame) => frame.outside).map((frame) => frame.frame),
      segments: trackSegments(keyframes, startFrame, stopFrame),
    };
  });
}

/** Whether `frame` falls inside any of `segments`. */
export function isPresent(segments: TrackSegment[], frame: number): boolean {
  return segments.some((segment) => frame >= segment.start && frame <= segment.stop);
}

/**
 * Where a frame sits along the range, as a 0–1 fraction, for positioning.
 *
 * A single-frame job is 0, not a division by zero.
 */
export function framePosition(frame: number, startFrame: number, stopFrame: number): number {
  const span = stopFrame - startFrame;
  if (span <= 0) return 0;
  return Math.min(1, Math.max(0, (frame - startFrame) / span));
}

/** The keyframe nearest `frame` in `direction`, or null when there is none that way. */
export function adjacentKeyframe(
  keyframes: number[],
  frame: number,
  direction: 1 | -1,
): number | null {
  const candidates =
    direction === 1
      ? keyframes.filter((candidate) => candidate > frame)
      : keyframes.filter((candidate) => candidate < frame).reverse();
  return candidates[0] ?? null;
}

/** Re-export so a caller can cross-check a row against the interpolator directly. */
export { interpolateTrack, type Keyframe };
