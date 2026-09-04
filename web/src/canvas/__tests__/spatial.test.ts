import { describe, expect, it } from 'vitest';
import { SpatialIndex } from '../spatial';
import type { Annotation, ShapeType } from '../types';

function box(id: string, x: number, y: number, size = 10, extra: Partial<Annotation> = {}): Annotation {
  return {
    id,
    labelId: 'label',
    frame: 0,
    shapeType: 'rectangle' as ShapeType,
    points: [x, y, x + size, y + size],
    rotation: 0,
    occluded: false,
    outside: false,
    zOrder: 0,
    source: 'manual',
    attributes: {},
    ...extra,
  };
}

describe('SpatialIndex', () => {
  it('bulk-loads and reports its size', () => {
    const index = new SpatialIndex();
    index.reset([box('a', 0, 0), box('b', 100, 100)]);
    expect(index.size).toBe(2);
  });

  it('returns only shapes intersecting the query box', () => {
    const index = new SpatialIndex();
    index.reset([box('near', 0, 0), box('far', 500, 500)]);

    const found = index.search({ minX: -5, minY: -5, maxX: 50, maxY: 50 });
    expect(found.map((a) => a.id)).toEqual(['near']);
  });

  it('culls to the viewport, which is what decouples cost from dataset size', () => {
    const index = new SpatialIndex();
    const annotations = Array.from({ length: 5_000 }, (_, i) =>
      box(`s${i}`, (i % 100) * 40, Math.floor(i / 100) * 40),
    );
    index.reset(annotations);

    const visible = index.search({ minX: 0, minY: 0, maxX: 200, maxY: 200 });
    expect(visible.length).toBeGreaterThan(0);
    expect(visible.length).toBeLessThan(60);
  });

  it('picks the shape under a point', () => {
    const index = new SpatialIndex();
    index.reset([box('a', 0, 0, 100)]);
    expect(index.pick({ x: 50, y: 50 }, 0)?.id).toBe('a');
    expect(index.pick({ x: 500, y: 500 }, 0)).toBeNull();
  });

  it('prefers the smaller shape when one sits inside another', () => {
    // Picking the enclosing shape is the single most irritating bug in this class of tool.
    const index = new SpatialIndex();
    index.reset([box('large', 0, 0, 200), box('small', 90, 90, 20)]);
    expect(index.pick({ x: 100, y: 100 }, 0)?.id).toBe('small');
  });

  it('prefers a higher z-order over a smaller area', () => {
    const index = new SpatialIndex();
    index.reset([
      box('small', 90, 90, 20, { zOrder: 0 }),
      box('large', 0, 0, 200, { zOrder: 5 }),
    ]);
    expect(index.pick({ x: 100, y: 100 }, 0)?.id).toBe('large');
  });

  it('honours a filter, so hidden or locked shapes are unpickable', () => {
    const index = new SpatialIndex();
    index.reset([box('hidden', 0, 0, 100)]);
    expect(index.pick({ x: 50, y: 50 }, 0, () => false)).toBeNull();
  });

  it('finds everything in a marquee box', () => {
    const index = new SpatialIndex();
    index.reset([box('a', 0, 0), box('b', 20, 20), box('c', 500, 500)]);
    const found = index.pickInBox({ minX: -5, minY: -5, maxX: 40, maxY: 40 });
    expect(found.map((a) => a.id).sort()).toEqual(['a', 'b']);
  });

  it('reflects updates so a moved shape is found in its new place', () => {
    const index = new SpatialIndex();
    index.reset([box('a', 0, 0)]);
    index.update(box('a', 300, 300));

    expect(index.pick({ x: 5, y: 5 }, 0)).toBeNull();
    expect(index.pick({ x: 305, y: 305 }, 0)?.id).toBe('a');
    expect(index.size).toBe(1);
  });

  it('removes a shape from both the tree and the lookup', () => {
    const index = new SpatialIndex();
    index.reset([box('a', 0, 0), box('b', 50, 50)]);
    index.remove('a');

    expect(index.size).toBe(1);
    expect(index.get('a')).toBeUndefined();
    expect(index.pick({ x: 5, y: 5 }, 0)).toBeNull();
  });

  it('ignores removal of an unknown id', () => {
    const index = new SpatialIndex();
    index.reset([box('a', 0, 0)]);
    expect(() => index.remove('nope')).not.toThrow();
    expect(index.size).toBe(1);
  });
});
