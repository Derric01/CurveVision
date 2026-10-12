/**
 * Client-side track interpolation.
 *
 * A deliberate mirror of `server/curvevision/services/interpolation.py`, and the shared
 * test vectors in `__tests__/interpolate.test.ts` check that the two agree. Duplicating
 * the algorithm is the cost of scrubbing a video without a round trip per frame; letting
 * the two implementations disagree would be far worse, so they are tested against the
 * same cases.
 */

import type { ApiSkeletonElement } from '@/api/types';
import type { ShapeType } from '@/canvas/types';

export interface Keyframe {
  frame: number;
  points: number[];
  rotation: number;
  occluded: boolean;
  outside: boolean;
  keyframe: boolean;
  /** A skeleton's joints: its geometry, since its `points` are empty. */
  elements?: ApiSkeletonElement[];
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
  elements?: ApiSkeletonElement[];
}

/**
 * A skeleton's joints at `t` between two keyframes, each joint on its own, matched by label.
 *
 * Mirrors `interpolate_elements` on the server: a joint absent at either end holds where it
 * was, as a whole object does approaching a departure. Copying the earlier keyframe's joints
 * (until iteration 68) froze a tracked skeleton between keyframes and then jumped it.
 */
export function interpolateElements(
  previous: readonly ApiSkeletonElement[],
  following: readonly ApiSkeletonElement[],
  t: number,
): ApiSkeletonElement[] {
  const later = new Map(following.map((element) => [element.label_id, element]));
  return previous.map((element) => {
    const target = later.get(element.label_id);
    const moving =
      target !== undefined &&
      !element.outside &&
      !target.outside &&
      element.points.length === target.points.length;
    return {
      ...element,
      points: moving
        ? element.points.map((value, index) => lerp(value, target.points[index]!, t))
        : [...element.points],
    };
  });
}

function copyElements(elements: readonly ApiSkeletonElement[] | undefined) {
  return elements ? { elements: elements.map((e) => ({ ...e, points: [...e.points] })) } : {};
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
            ...copyElements(candidate.elements),
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
      ...copyElements(previous.elements),
    };
  }

  const span = following.frame - previous.frame;
  const t = span === 0 ? 0 : (frame - previous.frame) / span;
  return {
    points: interpolatePoints(previous.points, following.points, t, shapeType),
    rotation: lerpAngle(previous.rotation, following.rotation, t),
    occluded: previous.occluded,
    keyframe: false,
    ...(previous.elements
      ? { elements: interpolateElements(previous.elements, following.elements ?? [], t) }
      : {}),
  };
}
