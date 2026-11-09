/**
 * The per-object controls: relabel, occluded, stacking order, hide and lock, and duplicate.
 *
 * The engine could relabel and toggle occluded before this; nothing called it. Stacking
 * order, per-object hide and lock are new. Each edit is one undo step and reaches autosave
 * through `annotationsChanged`; hide and lock are the session's own and save nothing.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AnnotationEngine } from '../engine';
import type { RendererLayers } from '../renderer';
import type { Annotation, AnnotationChange } from '../types';

type Setup = { engine: AnnotationEngine; changes: AnnotationChange[] };

function setUp(): Setup {
  const layer = {} as unknown as CanvasRenderingContext2D;
  const layers: RendererLayers = { media: layer, shapes: layer, overlay: layer };
  const changes: AnnotationChange[] = [];
  const engine = new AnnotationEngine({
    layers,
    listeners: { annotationsChanged: (change) => changes.push(change) },
  });
  engine.setLabels([
    { id: 'car', name: 'car', color: '#ef4444', visible: true, locked: false },
    { id: 'bus', name: 'bus', color: '#22c55e', visible: true, locked: false },
  ]);
  engine.setActiveLabel('car');
  engine.setMedia({ width: 400, height: 400, image: null }, { fit: false });
  return { engine, changes };
}

function draw(engine: AnnotationEngine, x: number, y: number): Annotation {
  engine.setTool('rectangle');
  engine.pointerDown({ x, y });
  engine.pointerMove({ x: x + 40, y: y + 40 });
  engine.pointerUp({ x: x + 40, y: y + 40 });
  engine.setTool('select');
  return engine.scene.all().at(-1)!;
}

describe('per-object controls', () => {
  beforeEach(() => {
    vi.stubGlobal('requestAnimationFrame', () => 0);
    vi.stubGlobal('cancelAnimationFrame', () => {});
  });
  afterEach(() => vi.unstubAllGlobals());

  it('brings an object in front of an overlapping one, so a click picks it, and saves that', () => {
    const { engine, changes } = setUp();
    const under = draw(engine, 100, 100);
    draw(engine, 110, 110);
    engine.scene.select([under.id]);

    engine.handleKey(']', {});

    const front = engine.scene.get(under.id)!;
    expect(front.zOrder).toBeGreaterThan(Math.max(...engine.scene.all().filter((a) => a.id !== under.id).map((a) => a.zOrder)));
    expect(engine.scene.pick({ x: 120, y: 120 }, 1)?.id).toBe(under.id);
    expect(changes.at(-1)?.updated.map((a) => a.zOrder)).toEqual([front.zOrder]);

    engine.handleKey('z', { ctrl: true });
    expect(engine.scene.get(under.id)?.zOrder).toBe(under.zOrder);
  });

  it('sends an object behind everything with [', () => {
    const { engine } = setUp();
    draw(engine, 100, 100);
    const top = draw(engine, 110, 110);
    engine.scene.select([top.id]);

    engine.handleKey('[', {});

    expect(engine.scene.get(top.id)!.zOrder).toBeLessThan(
      Math.min(...engine.scene.all().filter((a) => a.id !== top.id).map((a) => a.zOrder)),
    );
  });

  it('toggles occluded with Q and relabels the selection, each one undo step', () => {
    const { engine } = setUp();
    const box = draw(engine, 100, 100);
    engine.scene.select([box.id]);

    engine.handleKey('q', {});
    engine.relabelSelection('bus');
    expect(engine.scene.get(box.id)).toMatchObject({ occluded: true, labelId: 'bus' });

    engine.handleKey('z', { ctrl: true });
    expect(engine.scene.get(box.id)).toMatchObject({ occluded: true, labelId: 'car' });
    engine.handleKey('z', { ctrl: true });
    expect(engine.scene.get(box.id)).toMatchObject({ occluded: false, labelId: 'car' });
  });

  it('hides an object from the canvas and from clicks, without saving anything', () => {
    const { engine, changes } = setUp();
    const box = draw(engine, 100, 100);
    engine.scene.select([box.id]);
    const saved = changes.length;

    engine.setObjectHidden(box.id, true);

    expect(engine.scene.selected).toEqual([]);
    expect(engine.scene.pick({ x: 120, y: 120 }, 1)).toBeNull();
    expect(engine.scene.visible({ minX: 0, minY: 0, maxX: 400, maxY: 400 })).toEqual([]);
    expect(changes).toHaveLength(saved);

    engine.setObjectHidden(box.id, false);
    expect(engine.scene.pick({ x: 120, y: 120 }, 1)?.id).toBe(box.id);
  });

  it('keeps a locked object on screen but out of reach of a drag', () => {
    const { engine } = setUp();
    const box = draw(engine, 100, 100);

    engine.setObjectLocked(box.id, true);
    engine.pointerDown({ x: 120, y: 120 });
    engine.pointerMove({ x: 200, y: 200 });
    engine.pointerUp({ x: 200, y: 200 });

    expect(engine.scene.get(box.id)!.points).toEqual(box.points);
    expect(engine.scene.visible({ minX: 0, minY: 0, maxX: 400, maxY: 400 })).toHaveLength(1);
  });

  it('duplicates an ellipse at the same size, stopping at the edge of the image', () => {
    const { engine } = setUp();
    engine.setTool('ellipse');
    engine.pointerDown({ x: 380, y: 200 });
    engine.pointerMove({ x: 395, y: 210 });
    engine.pointerUp({ x: 395, y: 210 });
    engine.setTool('select');
    const original = engine.scene.all()[0]!;
    engine.scene.select([original.id]);

    engine.handleKey('d', { ctrl: true });

    const copy = engine.scene.all().find((a) => a.id !== original.id)!;
    expect(copy.points.slice(2)).toEqual(original.points.slice(2)); // same radii
    expect(copy.points[0]! + copy.points[2]!).toBeLessThanOrEqual(400); // inside the frame
  });
});
