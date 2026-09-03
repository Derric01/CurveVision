/**
 * Client-side track interpolation.
 *
 * A deliberate mirror of `server/curvevision/services/interpolation.py`, and the shared
 * test vectors in `__tests__/interpolate.test.ts` check that the two agree. Duplicating
 * the algorithm is the cost of scrubbing a video without a round trip per frame; letting
 * the two implementations disagree would be far worse, so they are tested against the
 * same cases.
 */

import type { ShapeType } from '@/canvas/types';

export interface Keyframe {
  frame: number;
  points: number[];
  rotation: number;
  occluded: boolean;
  outside: boolean;
  keyframe: boolean;
}

const PATH_LIKE: ReadonlySet<ShapeType> = new Set<ShapeType>(['polygon', 'polyline']);

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

/** Interpolate degrees along the shorter arc, so 350 -> 10 goes forward through 0. */
export function lerpAngle(a: number, b: number, t: number): number {
  const delta = (((b - a + 180) % 360) + 360) % 360 - 180;
  return a + delta * t;
}

/** Resample a path to exactly `count` vertices, evenly spaced by arc length. */
export function resample(points: number[], count: number, closed: boolean): number[] {
  const pairs: [number, number][] = [];
  for (let i = 0; i + 1 < points.length; i += 2) {
    pairs.push([points[i]!, points[i + 1]!]);
  }
  if (count <= 0 || pairs.length === 0) return [];
  if (pairs.length === 1) {
    return Array.from({ length: count * 2 }, (_, i) => pairs[0]![i % 2]!);
  }

  const ordered = closed ? [...pairs, pairs[0]!] : pairs;
  const lengths = [0];
  for (let i = 1; i < ordered.length; i++) {
    const [px, py] = ordered[i - 1]!;
    const [x, y] = ordered[i]!;
    lengths.push(lengths[i - 1]! + Math.hypot(x - px, y - py));
  }

  const total = lengths[lengths.length - 1]!;
  if (total === 0) {
    return Array.from({ length: count * 2 }, (_, i) => pairs[0]![i % 2]!);
  }

  const divisor = closed ? count : count - 1;
  const output: number[] = [];
  let segment = 0;
  for (let i = 0; i < count; i++) {
    const target = total * (i / divisor);
    while (segment < lengths.length - 2 && lengths[segment + 1]! < target) segment++;
    const span = lengths[segment + 1]! - lengths[segment]!;
    const t = span === 0 ? 0 : (target - lengths[segment]!) / span;
    const start = ordered[segment]!;
    const end = ordered[segment + 1]!;
    output.push(lerp(start[0], end[0], t), lerp(start[1], end[1], t));
  }
  return output;
}

export function interpolatePoints(
  start: number[],
  end: number[],
  t: number,
  shapeType: ShapeType,
): number[] {
  if (t <= 0) return [...start];
  if (t >= 1) return [...end];

  let from = start;
  let to = end;
  if (PATH_LIKE.has(shapeType) && start.length !== end.length) {
    // Pairing vertices by index scrambles the shape the moment an annotator inserts one;
    // resampling both to a common arc-length parameterisation is the correct fix.
    const closed = shapeType === 'polygon';
    const count = Math.max(start.length, end.length) / 2;
    from = resample(start, count, closed);
    to = resample(end, count, closed);
  }

  if (from.length !== to.length) return [...start];
  return from.map((value, index) => lerp(value, to[index]!, t));
}

export interface InterpolatedShape {
  points: number[];
  rotation: number;
  occluded: boolean;
  keyframe: boolean;
}

/** The track's shape on `frame`, or null when it is not present there. */
export function interpolateTrack(
  keyframes: Keyframe[],
  shapeType: ShapeType,
  frame: number,
): InterpolatedShape | null {
  if (keyframes.length === 0) return null;

  const sorted = [...keyframes].sort((a, b) => a.frame - b.frame);
  if (frame < sorted[0]!.frame) return null;

  for (const candidate of sorted) {
    if (candidate.frame === frame) {
      return candidate.outside
        ? null
        : {
            points: [...candidate.points],
            rotation: candidate.rotation,
            occluded: candidate.occluded,
            keyframe: true,
          };
    }
    if (candidate.frame > frame) break;
  }

  let previous: Keyframe | null = null;
  let following: Keyframe | null = null;
  for (const candidate of sorted) {
    if (candidate.frame < frame) previous = candidate;
    else if (!following) {
      following = candidate;
      break;
    }
  }

  if (!previous || previous.outside) return null;

  // Past the last keyframe, or approaching a departure: hold the last real position. The
  // coordinates stored on an `outside` keyframe are usually a stale copy, so interpolating
  // towards them would animate the object somewhere it never was.
  if (!following || following.outside) {
    return {
      points: [...previous.points],
      rotation: previous.rotation,
      occluded: previous.occluded,
      keyframe: false,
    };
  }

  const span = following.frame - previous.frame;
  const t = span === 0 ? 0 : (frame - previous.frame) / span;
  return {
    points: interpolatePoints(previous.points, following.points, t, shapeType),
    rotation: lerpAngle(previous.rotation, following.rotation, t),
    occluded: previous.occluded,
    keyframe: false,
  };
}
