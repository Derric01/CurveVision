/**
 * Moving and resizing keep an object the shape it was.
 *
 * Four defects, each confirmed against the old code:
 *
 * - A box pushed against the edge of the image was **squashed**, every coordinate clamped
 *   on its own: a 40-pixel box dragged right came back `[400, 100, 400, 140]`, no width.
 * - **Resizing** could shrink a box to a 1-pixel speck the drawing tools would refuse.
 * - **Moving an ellipse grew it**: `[cx, cy, rx, ry]` was translated pair by pair.
 * - **Moving a mask** moved its box and not its pixels.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AnnotationEngine } from '../engine';
import type { RendererLayers } from '../renderer';
import type { Annotation } from '../types';
import { clampMove, moveAnnotation, tooSmall } from '../tools';

function engine(): AnnotationEngine {
  const layer = {} as unknown as CanvasRenderingContext2D;
  const layers: RendererLayers = { media: layer, shapes: layer, overlay: layer };
  const created = new AnnotationEngine({ layers });
  created.setLabels([{ id: 'car', name: 'car', color: '#ef4444', visible: true, locked: false }]);
  created.setActiveLabel('car');
  created.setMedia({ width: 400, height: 400, image: null }, { fit: false });
  return created;
}

function drawBox(target: AnnotationEngine, x1: number, y1: number, x2: number, y2: number): void {
  target.setTool('rectangle');
  target.pointerDown({ x: x1, y: y1 });
  target.pointerMove({ x: x2, y: y2 });
  target.pointerUp({ x: x2, y: y2 });
  target.setTool('select');
}

function points(target: AnnotationEngine): number[] {
  return target.scene.all()[0]!.points;
}

function annotation(overrides: Partial<Annotation>): Annotation {
  return {
    id: 'a',
    labelId: 'car',
    frame: 0,
    shapeType: 'rectangle',
    points: [],
    occluded: false,
    attributes: {},
    ...overrides,
  } as Annotation;
}

describe('moving and resizing', () => {
  beforeEach(() => {
    vi.stubGlobal('requestAnimationFrame', () => 0);
    vi.stubGlobal('cancelAnimationFrame', () => {});
  });
  afterEach(() => vi.unstubAllGlobals());

  it('stops a box at the edge of the image instead of squashing it', () => {
    const target = engine();
    drawBox(target, 340, 100, 380, 140);

    target.pointerDown({ x: 360, y: 120 });
    for (let x = 370; x <= 460; x += 10) target.pointerMove({ x, y: 120 });
    target.pointerUp({ x: 460, y: 120 });

    expect(points(target)).toEqual([360, 100, 400, 140]); // 40 wide, against the edge
  });

  it('slides along an edge and comes back when the pointer does', () => {
    const target = engine();
    drawBox(target, 10, 10, 50, 50);

    target.pointerDown({ x: 30, y: 30 });
    target.pointerMove({ x: 0, y: 60 }); // left as far as it goes, and down
    expect(points(target)).toEqual([0, 40, 40, 80]);
    target.pointerMove({ x: 40, y: 40 });
    target.pointerUp({ x: 40, y: 40 });

    expect(points(target)).toEqual([20, 20, 60, 60]);
  });

  it('does not let a resize shrink a box below two pixels', () => {
    const target = engine();
    drawBox(target, 340, 100, 380, 140);
    target.scene.select([target.scene.all()[0]!.id]);

    target.pointerDown({ x: 380, y: 140 }); // the bottom-right handle
    target.pointerMove({ x: 341, y: 101 });
    target.pointerUp({ x: 341, y: 101 });

    const [x1, y1, x2, y2] = points(target);
    expect(x2! - x1!).toBeGreaterThanOrEqual(2);
    expect(y2! - y1!).toBeGreaterThanOrEqual(2);
  });

  it('moves an ellipse without changing its radii', () => {
    const moved = moveAnnotation(annotation({ shapeType: 'ellipse', points: [100, 100, 30, 20] }), 50, -10);
    expect(moved.points).toEqual([150, 90, 30, 20]);
  });

  it('moves a mask by whole pixels, pixels and box together', () => {
    const mask = { rle: [0, 4], left: 10, top: 20, width: 2, height: 2 };
    const moved = moveAnnotation(
      annotation({ shapeType: 'mask', points: [10, 20, 11, 21], mask }),
      5.4,
      -2.6,
    );
    expect(moved.mask).toEqual({ ...mask, left: 15, top: 17 });
    expect(moved.points).toEqual([15, 17, 16, 18]);
  });

  it('limits a selection by its combined bounds, so it moves as one', () => {
    const left = annotation({ points: [0, 0, 10, 10] });
    const right = annotation({ points: [380, 0, 395, 10] });
    expect(clampMove([left, right], 20, 0, 400, 400)).toEqual([5, 0]);
    expect(clampMove([left, right], -20, 0, 400, 400)).toEqual([0, 0]);
  });

  it('lets an object already partly outside come back in, never further out', () => {
    const outside = annotation({ points: [-20, 10, 30, 40] });
    expect(clampMove([outside], -5, 0, 400, 400)).toEqual([0, 0]);
    expect(clampMove([outside], 15, 0, 400, 400)).toEqual([15, 0]);
  });

  it('calls a box, an ellipse or a rotated box under two pixels too small', () => {
    expect(tooSmall(annotation({ points: [0, 0, 1, 50] }))).toBe(true);
    expect(tooSmall(annotation({ points: [0, 0, 2, 2] }))).toBe(false);
    expect(tooSmall(annotation({ shapeType: 'ellipse', points: [5, 5, 0.5, 10] }))).toBe(true);
    expect(tooSmall(annotation({ shapeType: 'polygon', points: [0, 0, 0, 0, 0, 0] }))).toBe(false);
  });
});
