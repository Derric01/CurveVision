/**
 * Geometry: bounding boxes, hit tests and transforms.
 *
 * Pure functions over plain numbers. Everything here runs inside a pointer-move handler,
 * so it allocates as little as it can get away with.
 */

import type { Annotation, Box, Point, ShapeType } from './types';

/** Bounding box of a flat coordinate list. */
export function boundsOf(points: number[]): Box {
  if (points.length < 2) {
    return { minX: 0, minY: 0, maxX: 0, maxY: 0 };
  }
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (let i = 0; i + 1 < points.length; i += 2) {
    const x = points[i]!;
    const y = points[i + 1]!;
    if (x < minX) minX = x;
    if (y < minY) minY = y;
    if (x > maxX) maxX = x;
    if (y > maxY) maxY = y;
  }
  return { minX, minY, maxX, maxY };
}

/**
 * Bounding box of an annotation, accounting for shape semantics.
 *
 * An ellipse stores `[cx, cy, rx, ry]`, so its raw coordinate extent is not its bounds --
 * getting this wrong makes ellipses fall out of the spatial index and become unselectable.
 */
export function annotationBounds(annotation: Annotation): Box {
  const { shapeType, points } = annotation;
  if (shapeType === 'ellipse' && points.length >= 4) {
    const [cx, cy, rx, ry] = points as [number, number, number, number];
    return { minX: cx - rx, minY: cy - ry, maxX: cx + rx, maxY: cy + ry };
  }
  const box = boundsOf(points);
  if (annotation.rotation && shapeType === 'rotated_rectangle') {
    return rotatedBounds(box, annotation.rotation);
  }
  return box;
}

/** Bounds of `box` rotated by `degrees` about its own centre. */
export function rotatedBounds(box: Box, degrees: number): Box {
  const radians = (degrees * Math.PI) / 180;
  const cos = Math.abs(Math.cos(radians));
  const sin = Math.abs(Math.sin(radians));
  const cx = (box.minX + box.maxX) / 2;
  const cy = (box.minY + box.maxY) / 2;
  const halfWidth = (box.maxX - box.minX) / 2;
  const halfHeight = (box.maxY - box.minY) / 2;
  const rotatedHalfWidth = halfWidth * cos + halfHeight * sin;
  const rotatedHalfHeight = halfWidth * sin + halfHeight * cos;
  return {
    minX: cx - rotatedHalfWidth,
    minY: cy - rotatedHalfHeight,
    maxX: cx + rotatedHalfWidth,
    maxY: cy + rotatedHalfHeight,
  };
}

export function boxesIntersect(a: Box, b: Box): boolean {
  return a.minX <= b.maxX && a.maxX >= b.minX && a.minY <= b.maxY && a.maxY >= b.minY;
}

export function boxContains(box: Box, point: Point, tolerance = 0): boolean {
  return (
    point.x >= box.minX - tolerance &&
    point.x <= box.maxX + tolerance &&
    point.y >= box.minY - tolerance &&
    point.y <= box.maxY + tolerance
  );
}

export function expandBox(box: Box, by: number): Box {
  return {
    minX: box.minX - by,
    minY: box.minY - by,
    maxX: box.maxX + by,
    maxY: box.maxY + by,
  };
}

/** Order a rectangle's corners so `x1 <= x2` and `y1 <= y2`. */
export function normalizeRectangle(points: number[]): number[] {
  const [x1 = 0, y1 = 0, x2 = 0, y2 = 0] = points;
  return [Math.min(x1, x2), Math.min(y1, y2), Math.max(x1, x2), Math.max(y1, y2)];
}

export function distance(a: Point, b: Point): number {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

/** Shortest distance from `point` to the segment `a`-`b`. */
export function distanceToSegment(point: Point, a: Point, b: Point): number {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const lengthSquared = dx * dx + dy * dy;
  if (lengthSquared === 0) return distance(point, a);
  let t = ((point.x - a.x) * dx + (point.y - a.y) * dy) / lengthSquared;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(point.x - (a.x + t * dx), point.y - (a.y + t * dy));
}

/** Even-odd point-in-polygon test. */
export function pointInPolygon(point: Point, points: number[]): boolean {
  let inside = false;
  const count = points.length / 2;
  for (let i = 0, j = count - 1; i < count; j = i++) {
    const xi = points[i * 2]!;
    const yi = points[i * 2 + 1]!;
    const xj = points[j * 2]!;
    const yj = points[j * 2 + 1]!;
    const intersects =
      yi > point.y !== yj > point.y &&
      point.x < ((xj - xi) * (point.y - yi)) / (yj - yi) + xi;
    if (intersects) inside = !inside;
  }
  return inside;
}

/**
 * Precise hit test against one annotation.
 *
 * `tolerance` is in image space; callers convert a screen-pixel tolerance through the
 * viewport scale so picking stays equally forgiving at every zoom level.
 */
export function hitTest(annotation: Annotation, point: Point, tolerance: number): boolean {
  const { shapeType, points } = annotation;
  switch (shapeType) {
    case 'rectangle':
    case 'rotated_rectangle':
    case 'cuboid':
    case 'mask':
      return boxContains(annotationBounds(annotation), point, tolerance);

    case 'ellipse': {
      const [cx = 0, cy = 0, rx = 0, ry = 0] = points;
      if (rx <= 0 || ry <= 0) return false;
      const nx = (point.x - cx) / (rx + tolerance);
      const ny = (point.y - cy) / (ry + tolerance);
      return nx * nx + ny * ny <= 1;
    }

    case 'polygon': {
      if (pointInPolygon(point, points)) return true;
      // Falling through to the edge test makes a thin or hollow polygon selectable by its
      // outline, which is how annotators expect to grab one.
      return nearAnyEdge(point, points, tolerance, true);
    }

    case 'polyline':
      return nearAnyEdge(point, points, tolerance, false);

    case 'points':
    case 'skeleton':
      for (let i = 0; i + 1 < points.length; i += 2) {
        if (distance(point, { x: points[i]!, y: points[i + 1]! }) <= tolerance + 4) {
          return true;
        }
      }
      return false;

    default:
      return boxContains(annotationBounds(annotation), point, tolerance);
  }
}

function nearAnyEdge(
  point: Point,
  points: number[],
  tolerance: number,
  closed: boolean,
): boolean {
  const count = points.length / 2;
  if (count < 2) return false;
  const last = closed ? count : count - 1;
  for (let i = 0; i < last; i++) {
    const j = (i + 1) % count;
    const a = { x: points[i * 2]!, y: points[i * 2 + 1]! };
    const b = { x: points[j * 2]!, y: points[j * 2 + 1]! };
    if (distanceToSegment(point, a, b) <= tolerance) return true;
  }
  return false;
}

/** Index of the vertex within `tolerance` of `point`, or -1. */
export function findVertex(points: number[], point: Point, tolerance: number): number {
  for (let i = 0; i + 1 < points.length; i += 2) {
    if (distance(point, { x: points[i]!, y: points[i + 1]! }) <= tolerance) {
      return i / 2;
    }
  }
  return -1;
}

/** Translate every coordinate by `(dx, dy)`. */
export function translatePoints(points: number[], dx: number, dy: number): number[] {
  const moved = new Array<number>(points.length);
  for (let i = 0; i + 1 < points.length; i += 2) {
    moved[i] = points[i]! + dx;
    moved[i + 1] = points[i + 1]! + dy;
  }
  return moved;
}

/** Clamp every coordinate into `[0, width] x [0, height]`. */
export function clampToImage(points: number[], width: number, height: number): number[] {
  const clamped = new Array<number>(points.length);
  for (let i = 0; i + 1 < points.length; i += 2) {
    clamped[i] = Math.max(0, Math.min(width, points[i]!));
    clamped[i + 1] = Math.max(0, Math.min(height, points[i + 1]!));
  }
  return clamped;
}

/** Snap `point` to the nearest vertex of `candidates` within `tolerance`. */
export function snapToVertex(
  point: Point,
  candidates: readonly Annotation[],
  tolerance: number,
): Point {
  let best = point;
  let bestDistance = tolerance;
  for (const annotation of candidates) {
    const { points } = annotation;
    for (let i = 0; i + 1 < points.length; i += 2) {
      const candidate = { x: points[i]!, y: points[i + 1]! };
      const d = distance(point, candidate);
      if (d < bestDistance) {
        bestDistance = d;
        best = candidate;
      }
    }
  }
  return best;
}

export function shapeIsClosed(shapeType: ShapeType): boolean {
  return shapeType === 'polygon';
}

/** Area of a shape in image pixels, used to break ties when shapes overlap. */
export function areaOf(annotation: Annotation): number {
  const box = annotationBounds(annotation);
  return Math.max(0, box.maxX - box.minX) * Math.max(0, box.maxY - box.minY);
}
