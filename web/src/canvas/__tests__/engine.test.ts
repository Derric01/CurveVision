import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AnnotationEngine } from '../engine';
import type { RendererLayers } from '../renderer';
import type { Annotation, LabelStyle } from '../types';

/**
 * The engine has never had a unit test: it schedules its repaint through
 * `requestAnimationFrame`, which plain Node (this suite's environment -- see `vitest.config`)
 * does not define, and painting needs a real 2D context this environment has no DOM to
 * produce. Neither is actually needed here. `applyResult` -- the thing under test -- runs
 * synchronously inside `pointerUp`, before any repaint is scheduled, so a
 * `requestAnimationFrame` stub that records a callback and never calls it is enough: nothing
 * ever reaches the fake layer contexts below, which is what lets them be empty objects
 * instead of a real Canvas2D implementation.
 */
function stubAnimationFrame(): void {
  vi.stubGlobal('requestAnimationFrame', () => 0);
  vi.stubGlobal('cancelAnimationFrame', () => {});
}

function fakeLayers(): RendererLayers {
  const layer = {} as unknown as CanvasRenderingContext2D;
  return { media: layer, shapes: layer, overlay: layer };
}

function label(id = 'l1'): LabelStyle {
  return { id, name: 'car', color: '#ef4444', visible: true, locked: false };
}

/**
 * A frame to draw on. `RectangleTool` clamps every point to `imageSize()`, and the default
 * (before anything calls `setMedia`) is `0x0` -- a box drawn against it collapses to a point
 * and `RectangleTool.onPointerUp` discards it as degenerate, which looks like "the tool
 * silently did nothing" rather than "the test forgot to set up a frame."
 */
function setUpFrame(engine: AnnotationEngine): void {
  // `fit: false`: the default viewport (scale 1, no offset) is what makes screen and image
  // coordinates the same number in these tests, and `fitToImage` would recompute it.
  engine.setMedia({ width: 100, height: 100, image: null }, { fit: false });
}

function drawRectangle(engine: AnnotationEngine): void {
  engine.pointerDown({ x: 10, y: 10 });
  engine.pointerMove({ x: 40, y: 40 });
  engine.pointerUp({ x: 40, y: 40 });
}

describe('AnnotationEngine: which frame a new shape belongs to', () => {
  beforeEach(stubAnimationFrame);
  afterEach(() => vi.unstubAllGlobals());

  /**
   * The regression this pins. The engine had no notion of "current frame" at all: every
   * tool builds its draft through `draftAnnotation`, which sets `frame: 0` as a placeholder,
   * and nothing corrected it. A live browser probe confirmed the failure mode exactly:
   * navigate to frame 2 of a 3-frame job, draw a rectangle, save, and read it back -- it
   * came back stored at frame 0. The shape did not error or warn; it simply was not there
   * the next time frame 2 was opened. Single-frame tasks -- every browser harness before
   * this one -- cannot expose this, because frame 0 is also the only frame.
   */
  it('stamps a newly created shape with the frame setFrame was told, not 0', () => {
    const created: Annotation[] = [];
    const engine = new AnnotationEngine({
      layers: fakeLayers(),
      listeners: { created: (annotation) => created.push(annotation) },
    });
    engine.setLabels([label()]);
    engine.setActiveLabel('l1');
    engine.setTool('rectangle');
    setUpFrame(engine);

    engine.setFrame(2);
    drawRectangle(engine);

    expect(created).toHaveLength(1);
    expect(created[0]?.frame).toBe(2);
  });

  it('defaults to frame 0 when nothing has called setFrame, matching a job opened on its first frame', () => {
    const created: Annotation[] = [];
    const engine = new AnnotationEngine({
      layers: fakeLayers(),
      listeners: { created: (annotation) => created.push(annotation) },
    });
    engine.setLabels([label()]);
    engine.setActiveLabel('l1');
    engine.setTool('rectangle');
    setUpFrame(engine);

    drawRectangle(engine);

    expect(created[0]?.frame).toBe(0);
  });

  // The bug's exact shape: frame changes, then a shape is drawn. `setAnnotations` (loading
  // the new frame's existing shapes) and `setFrame` are two different calls in
  // `AnnotationCanvas`, and only one of them makes the new shape land correctly.
  it('follows setFrame across repeated frame changes, independent of what is loaded', () => {
    const created: Annotation[] = [];
    const engine = new AnnotationEngine({
      layers: fakeLayers(),
      listeners: { created: (annotation) => created.push(annotation) },
    });
    engine.setLabels([label()]);
    engine.setActiveLabel('l1');
    engine.setTool('rectangle');
    setUpFrame(engine);

    engine.setFrame(5);
    engine.setAnnotations([]); // the frame-5 document happens to be empty
    drawRectangle(engine);

    engine.setFrame(1);
    engine.setAnnotations([]);
    drawRectangle(engine);

    expect(created.map((a) => a.frame)).toEqual([5, 1]);
  });

  it('does not alter frame for an update to an existing annotation', () => {
    // Regression guard the other way: the fix touches only `result.created` in
    // `applyResult`. An edit to something already on the scene must keep going through
    // untouched, or a move/resize on frame 5 could end up "corrected" onto frame 0 by the
    // very fix meant to stop shapes landing on frame 0.
    const updates: Annotation[] = [];
    const engine = new AnnotationEngine({
      layers: fakeLayers(),
      listeners: { annotationsChanged: (change) => updates.push(...change.updated) },
    });
    engine.setLabels([label()]);
    setUpFrame(engine);
    engine.setFrame(5);
    engine.setAnnotations([
      {
        id: 'existing',
        labelId: 'l1',
        frame: 5,
        shapeType: 'rectangle',
        points: [0, 0, 10, 10],
        rotation: 0,
        occluded: false,
        outside: false,
        zOrder: 0,
        source: 'manual',
        attributes: {},
      },
    ]);
    engine.setTool('select');
    engine.scene.select(['existing']);

    engine.pointerDown({ x: 5, y: 5 });
    engine.pointerMove({ x: 8, y: 8 });
    engine.pointerUp({ x: 8, y: 8 });

    expect(updates).toHaveLength(1);
    expect(updates[0]?.frame).toBe(5);
  });
});
