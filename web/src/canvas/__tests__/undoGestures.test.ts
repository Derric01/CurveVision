/**
 * Undo, one gesture at a time, over long sequences of edits.
 *
 * Consecutive edits to the same object merge into one undo step, which is how a drag's
 * hundred pointer-moves become one Ctrl+Z. Nothing marked where a gesture ended, so a
 * *second* drag of the same box merged into the first and one Ctrl+Z undid both; two
 * relabels or two occlusion toggles in a row did the same. `CommandStack.seal` is that
 * boundary, set on every press and around every one-off action.
 *
 * The last test drives 190 random edits (the history keeps 200), keeps the scene after
 * each, then undoes all of them checking every step on the way back, and redoes them.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AnnotationEngine } from '../engine';
import type { RendererLayers } from '../renderer';
import type { Annotation } from '../types';

function engineWithLabels(): AnnotationEngine {
  const layer = {} as unknown as CanvasRenderingContext2D;
  const layers: RendererLayers = { media: layer, shapes: layer, overlay: layer };
  const engine = new AnnotationEngine({ layers });
  engine.setLabels([
    { id: 'car', name: 'car', color: '#ef4444', visible: true, locked: false },
    { id: 'bus', name: 'bus', color: '#22c55e', visible: true, locked: false },
  ]);
  engine.setActiveLabel('car');
  engine.setMedia({ width: 400, height: 400, image: null }, { fit: false });
  return engine;
}

function draw(engine: AnnotationEngine, x: number, y: number, size = 30): void {
  engine.setTool('rectangle');
  engine.pointerDown({ x, y });
  engine.pointerMove({ x: x + size, y: y + size });
  engine.pointerUp({ x: x + size, y: y + size });
  engine.setTool('select');
}

/** Press inside the box at `(x, y)` and drag it by `(dx, dy)` in steps, like a mouse. */
function drag(engine: AnnotationEngine, x: number, y: number, dx: number, dy: number): void {
  engine.setTool('select');
  engine.pointerDown({ x, y });
  for (let step = 1; step <= 5; step += 1) {
    engine.pointerMove({ x: x + (dx * step) / 5, y: y + (dy * step) / 5 });
  }
  engine.pointerUp({ x: x + dx, y: y + dy });
}

function box(engine: AnnotationEngine): number[] {
  return engine.scene.all()[0]?.points ?? [];
}

/** What the user would see: every object, independent of insertion order. */
function snapshot(engine: AnnotationEngine): string {
  return JSON.stringify(
    engine.scene
      .all()
      .map((a: Annotation) => ({ id: a.id, label: a.labelId, points: a.points, occluded: a.occluded }))
      .sort((a, b) => a.id.localeCompare(b.id)),
  );
}

const undo = (engine: AnnotationEngine) => engine.handleKey('z', { ctrl: true });
const redo = (engine: AnnotationEngine) => engine.handleKey('z', { ctrl: true, shift: true });

describe('undo: one step per gesture', () => {
  beforeEach(() => {
    vi.stubGlobal('requestAnimationFrame', () => 0);
    vi.stubGlobal('cancelAnimationFrame', () => {});
  });
  afterEach(() => vi.unstubAllGlobals());

  it('takes one drag back as a whole', () => {
    const engine = engineWithLabels();
    draw(engine, 10, 10);
    drag(engine, 25, 25, 20, 20);
    expect(box(engine)).toEqual([30, 30, 60, 60]);

    undo(engine);

    expect(box(engine)).toEqual([10, 10, 40, 40]);
  });

  it('takes two drags of the same box back one at a time', () => {
    // Failed before the fix: one undo jumped straight back to [10, 10, 40, 40].
    const engine = engineWithLabels();
    draw(engine, 10, 10);
    drag(engine, 25, 25, 20, 20);
    drag(engine, 45, 45, 30, 30);
    expect(box(engine)).toEqual([60, 60, 90, 90]);

    undo(engine);
    expect(box(engine)).toEqual([30, 30, 60, 60]);
    undo(engine);
    expect(box(engine)).toEqual([10, 10, 40, 40]);
    redo(engine);
    expect(box(engine)).toEqual([30, 30, 60, 60]);
  });

  it('takes two relabels and two occlusion toggles back one at a time', () => {
    const engine = engineWithLabels();
    draw(engine, 10, 10);
    engine.scene.select([engine.scene.all()[0]!.id]);
    engine.relabelSelection('bus');
    engine.relabelSelection('car');
    engine.toggleOccluded();
    engine.toggleOccluded();

    const states: string[] = [];
    for (let step = 0; step < 4; step += 1) {
      undo(engine);
      const [annotation] = engine.scene.all();
      states.push(`${annotation?.labelId}/${annotation?.occluded}`);
    }

    expect(states).toEqual(['car/true', 'car/false', 'bus/false', 'car/false']);
  });

  it('survives 190 random edits undone and redone step by step', () => {
    // A seeded generator, so a failure reproduces.
    let seed = 20261109;
    const random = () => {
      seed = (seed * 1103515245 + 12345) % 2147483648;
      return seed / 2147483648;
    };
    const engine = engineWithLabels();
    const states = [snapshot(engine)];

    for (let step = 0; step < 190; step += 1) {
      const objects = engine.scene.all();
      const pick = objects[Math.floor(random() * objects.length)];
      const choice = objects.length === 0 ? 0 : Math.floor(random() * 5);
      const before = snapshot(engine);
      if (choice === 0 || !pick) {
        // Spread over the frame and apart, so a press lands on the box meant.
        const slot = Math.floor(random() * 25);
        draw(engine, 20 + (slot % 5) * 70, 20 + Math.floor(slot / 5) * 70, 20);
      } else {
        const [x1, y1] = pick.points as [number, number, number, number];
        engine.scene.select([pick.id]);
        if (choice === 1) drag(engine, x1 + 5, y1 + 5, 3, 2);
        else if (choice === 2) engine.relabelSelection(pick.labelId === 'car' ? 'bus' : 'car');
        else if (choice === 3) engine.toggleOccluded();
        else engine.handleKey('Delete', {});
      }
      const after = snapshot(engine);
      if (after !== before) states.push(after); // an edit that changed nothing records nothing
    }
    expect(states.length).toBeGreaterThan(120);

    for (let index = states.length - 2; index >= 0; index -= 1) {
      undo(engine);
      expect(snapshot(engine), `undoing back to state ${index}`).toBe(states[index]);
    }
    for (let index = 1; index < states.length; index += 1) {
      redo(engine);
      expect(snapshot(engine), `redoing to state ${index}`).toBe(states[index]);
    }
  });
});
