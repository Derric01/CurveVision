import { describe, expect, it } from 'vitest';
import {
  annotationBounds,
  boundsOf,
  clampToImage,
  distanceToSegment,
  findVertex,
  hitTest,
  normalizeRectangle,
  pointInPolygon,
  rotatedBounds,
  snapToVertex,
  translatePoints,
} from '../geometry';
import type { Annotation, ShapeType } from '../types';

function shape(shapeType: ShapeType, points: number[], extra: Partial<Annotation> = {}): Annotation {
  return {
    id: 'a',
    labelId: 'label',
    frame: 0,
    shapeType,
    points,
    rotation: 0,
    occluded: false,
    outside: false,
    zOrder: 0,
    source: 'manual',
    attributes: {},
    ...extra,
  };
}

describe('boundsOf', () => {
  it('finds the extent of a coordinate list', () => {
    expect(boundsOf([10, 20, 5, 40, 30, 15])).toEqual({
      minX: 5,
      minY: 15,
      maxX: 30,
      maxY: 40,
    });
  });

  it('handles an empty list without producing Infinity', () => {
    expect(boundsOf([])).toEqual({ minX: 0, minY: 0, maxX: 0, maxY: 0 });
  });
});

describe('annotationBounds', () => {
  it('expands an ellipse from centre and radii', () => {
    // The raw coordinates are [cx, cy, rx, ry]; treating them as an extent would put the
    // ellipse in the wrong place in the spatial index and make it unselectable.
    expect(annotationBounds(shape('ellipse', [100, 100, 30, 20]))).toEqual({
      minX: 70,
      minY: 80,
      maxX: 130,
      maxY: 120,
    });
  });

  it('grows a rotated rectangle to cover its rotated corners', () => {
    const bounds = annotationBounds(
      shape('rotated_rectangle', [0, 0, 100, 50], { rotation: 45 }),
    );
    expect(bounds.maxX - bounds.minX).toBeGreaterThan(100);
    expect(bounds.maxY - bounds.minY).toBeGreaterThan(50);
  });

  it('leaves an unrotated shape alone', () => {
    expect(annotationBounds(shape('rectangle', [10, 10, 50, 40]))).toEqual({
      minX: 10,
      minY: 10,
      maxX: 50,
      maxY: 40,
    });
  });
});

describe('rotatedBounds', () => {
  it('is a no-op at zero degrees', () => {
    const box = { minX: 0, minY: 0, maxX: 10, maxY: 20 };
    const rotated = rotatedBounds(box, 0);
    expect(rotated.maxX - rotated.minX).toBeCloseTo(10);
    expect(rotated.maxY - rotated.minY).toBeCloseTo(20);
  });

  it('swaps the axes at ninety degrees', () => {
    const rotated = rotatedBounds({ minX: 0, minY: 0, maxX: 10, maxY: 20 }, 90);
    expect(rotated.maxX - rotated.minX).toBeCloseTo(20);
    expect(rotated.maxY - rotated.minY).toBeCloseTo(10);
  });
});

describe('hitTest', () => {
  it('accepts a point inside a rectangle', () => {
    expect(hitTest(shape('rectangle', [0, 0, 100, 100]), { x: 50, y: 50 }, 0)).toBe(true);
  });

  it('rejects a point outside a rectangle', () => {
    expect(hitTest(shape('rectangle', [0, 0, 100, 100]), { x: 150, y: 50 }, 0)).toBe(false);
  });

  it('honours the tolerance near an edge', () => {
    const rectangle = shape('rectangle', [0, 0, 100, 100]);
    expect(hitTest(rectangle, { x: 104, y: 50 }, 0)).toBe(false);
    expect(hitTest(rectangle, { x: 104, y: 50 }, 6)).toBe(true);
  });

  it('uses the ellipse equation rather than its bounding box', () => {
    const ellipse = shape('ellipse', [100, 100, 50, 20]);
    expect(hitTest(ellipse, { x: 100, y: 100 }, 0)).toBe(true);
    // Inside the bounding box, outside the ellipse itself.
    expect(hitTest(ellipse, { x: 148, y: 118 }, 0)).toBe(false);
  });

  it('accepts a point inside a polygon', () => {
    const triangle = shape('polygon', [0, 0, 100, 0, 50, 100]);
    expect(hitTest(triangle, { x: 50, y: 30 }, 0)).toBe(true);
    expect(hitTest(triangle, { x: 5, y: 90 }, 0)).toBe(false);
  });

  it('lets a polygon be grabbed by its outline', () => {
    const triangle = shape('polygon', [0, 0, 100, 0, 50, 100]);
    expect(hitTest(triangle, { x: 2, y: 0 }, 3)).toBe(true);
  });

  it('matches a polyline only near its segments', () => {
    const line = shape('polyline', [0, 0, 100, 0]);
    expect(hitTest(line, { x: 50, y: 2 }, 4)).toBe(true);
    expect(hitTest(line, { x: 50, y: 40 }, 4)).toBe(false);
  });

  it('matches point annotations near a vertex', () => {
    const points = shape('points', [10, 10, 80, 80]);
    expect(hitTest(points, { x: 12, y: 12 }, 2)).toBe(true);
    expect(hitTest(points, { x: 45, y: 45 }, 2)).toBe(false);
  });
});

describe('pointInPolygon', () => {
  it('handles a concave polygon correctly', () => {
    // An L-shape: the notch must read as outside.
    const polygon = [0, 0, 100, 0, 100, 40, 40, 40, 40, 100, 0, 100];
    expect(pointInPolygon({ x: 20, y: 20 }, polygon)).toBe(true);
    expect(pointInPolygon({ x: 80, y: 80 }, polygon)).toBe(false);
  });
});

describe('distanceToSegment', () => {
  it('measures perpendicular distance inside the segment', () => {
    expect(distanceToSegment({ x: 5, y: 3 }, { x: 0, y: 0 }, { x: 10, y: 0 })).toBeCloseTo(3);
  });

  it('clamps to the endpoints outside the segment', () => {
    expect(distanceToSegment({ x: -4, y: 0 }, { x: 0, y: 0 }, { x: 10, y: 0 })).toBeCloseTo(4);
  });

  it('handles a degenerate segment', () => {
    expect(distanceToSegment({ x: 3, y: 4 }, { x: 0, y: 0 }, { x: 0, y: 0 })).toBeCloseTo(5);
  });
});

describe('normalizeRectangle', () => {
  it('orders corners dragged up and to the left', () => {
    expect(normalizeRectangle([100, 100, 10, 20])).toEqual([10, 20, 100, 100]);
  });
});

describe('findVertex', () => {
  it('returns the index of a nearby vertex', () => {
    expect(findVertex([0, 0, 50, 50, 100, 0], { x: 51, y: 49 }, 5)).toBe(1);
  });

  it('returns -1 when nothing is close', () => {
    expect(findVertex([0, 0, 50, 50], { x: 25, y: 25 }, 3)).toBe(-1);
  });
});

describe('translatePoints and clampToImage', () => {
  it('moves every coordinate', () => {
    expect(translatePoints([0, 0, 10, 10], 5, -3)).toEqual([5, -3, 15, 7]);
  });

  it('keeps geometry inside the frame', () => {
    expect(clampToImage([-10, -10, 200, 200], 100, 80)).toEqual([0, 0, 100, 80]);
  });
});

describe('snapToVertex', () => {
  it('snaps to a nearby vertex of another shape', () => {
    const neighbour = shape('polygon', [10, 10, 90, 10, 90, 90]);
    expect(snapToVertex({ x: 12, y: 11 }, [neighbour], 5)).toEqual({ x: 10, y: 10 });
  });

  it('leaves a distant point untouched', () => {
    const neighbour = shape('polygon', [10, 10, 90, 10, 90, 90]);
    const point = { x: 50, y: 50 };
    expect(snapToVertex(point, [neighbour], 5)).toBe(point);
  });
});
