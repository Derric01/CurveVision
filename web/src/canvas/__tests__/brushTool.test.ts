/**
 * The brush tool's state machine.
 *
 * `mask.test.ts` proves the pixel functions this tool paints with; this proves the tool
 * around them: which stroke starts a new mask and which edits the selected one, that
 * erasing something down to no pixels at all deletes the object rather than leaving an
 * invalid empty mask behind, that the original is hidden from the scene for the length of
 * an edit so an eraser does not appear to do nothing, and that a stroke a tool cannot run
 * (no active label, no frame loaded) is refused rather than quietly producing a shape.
 */

import { describe, expect, it } from 'vitest';
import { maskArea, maskContains } from '../mask';
import { Scene } from '../scene';
import { BrushTool, createTool, TOOL_SHORTCUTS, type ToolContext } from '../tools';
import { createViewport } from '../viewport';
import type { Annotation, MaskData, PointerInput } from '../types';

function context(
  scene: Scene,
  overrides: { labelId?: string | null; width?: number; height?: number } = {},
): ToolContext {
  const { labelId = 'car', width = 40, height = 40 } = overrides;
  return {
    scene,
    viewport: () => createViewport(),
    imageSize: () => ({ width, height }),
    activeLabelId: () => labelId,
    snapEnabled: () => false,
  };
}

function point(x: number, y: number, extra: Partial<PointerInput> = {}): PointerInput {
  return { image: { x, y }, screen: { x, y }, shiftKey: false, ctrlKey: false, altKey: false, button: 0, ...extra };
}

function existingMask(
  id = 'm1',
  labelId = 'car',
  mask: MaskData = { rle: [0, 100], left: 10, top: 10, width: 10, height: 10 },
): Annotation {
  return {
    id,
    labelId,
    frame: 0,
    shapeType: 'mask',
    points: [10, 10, 19, 19],
    rotation: 0,
    occluded: false,
    outside: false,
    zOrder: 0,
    source: 'manual',
    attributes: {},
    mask,
  };
}

describe('starting a new mask', () => {
  it('refuses to draw without an active label', () => {
    const tool = new BrushTool();
    const result = tool.onPointerDown(point(10, 10), context(new Scene({}), { labelId: null }));
    expect(result).toEqual({});
  });

  it('refuses to draw before a frame is loaded', () => {
    const tool = new BrushTool();
    const result = tool.onPointerDown(point(10, 10), context(new Scene({}), { width: 0, height: 0 }));
    expect(result).toEqual({});
  });

  it('creates a mask covering the clicked point once the stroke ends', () => {
    const tool = new BrushTool();
    const ctx = context(new Scene({}));
    tool.onPointerDown(point(15, 15), ctx);
    tool.onPointerMove(point(18, 15));
    const result = tool.onPointerUp();

    expect(result.created).toBeDefined();
    expect(result.created?.shapeType).toBe('mask');
    expect(result.created?.labelId).toBe('car');
    expect(maskContains(result.created?.mask, 15, 15)).toBe(true);
    expect(maskContains(result.created?.mask, 18, 15)).toBe(true);
  });

  it('leaves the frame at 0, for the engine to correct -- see engine.test.ts', () => {
    // The brush is a tool like any other: it does not know the current frame, and
    // `AnnotationEngine.applyResult` is what stamps the real one in. Asserting the
    // placeholder here is what would catch a future brush-specific frame shortcut that
    // bypassed that shared fix.
    const tool = new BrushTool();
    const ctx = context(new Scene({}));
    tool.onPointerDown(point(15, 15), ctx);
    const result = tool.onPointerUp();
    expect(result.created?.frame).toBe(0);
  });

  it('discards a stroke that painted nothing, the same way a degenerate click is discarded elsewhere', () => {
    // Erasing when there was never anything to erase: the buffer stays entirely empty.
    const tool = new BrushTool();
    const ctx = context(new Scene({}));
    tool.onPointerDown(point(15, 15), ctx, );
    // Re-issue the down as an erase by starting a fresh tool instance -- `onPointerDown`
    // reads the modifiers on the event that starts the stroke.
    const eraseTool = new BrushTool();
    eraseTool.onPointerDown(point(15, 15, { altKey: true }), context(new Scene({})));
    const result = eraseTool.onPointerUp();
    expect(result.created).toBeUndefined();
    expect(result.deleted).toBeUndefined();
  });

  it('adjusts its radius with [ and ], clamped, and reports it for the status line', () => {
    const tool = new BrushTool();
    expect(tool.brushRadius).toBe(14);
    tool.onKey('[');
    expect(tool.brushRadius).toBe(12);
    for (let i = 0; i < 10; i += 1) tool.onKey('[');
    expect(tool.brushRadius).toBeGreaterThanOrEqual(2); // MIN_RADIUS, not zero or negative
    for (let i = 0; i < 100; i += 1) tool.onKey(']');
    expect(tool.brushRadius).toBeLessThanOrEqual(150); // MAX_RADIUS
  });

  it('leaves every other key unhandled', () => {
    const tool = new BrushTool();
    expect(tool.onKey('Enter')).toBeNull();
    expect(tool.onKey('a')).toBeNull();
  });
});

describe('editing an existing mask', () => {
  it('is chosen by selecting a mask of the active label first, not by clicking on it', () => {
    const scene = new Scene({});
    const original = existingMask();
    scene.add(original);
    scene.select([original.id]);

    const tool = new BrushTool();
    // Painting somewhere that does not even overlap the existing mask still edits it,
    // because the selection -- not the click position -- is what says which mask this is.
    tool.onPointerDown(point(30, 30), context(scene));
    const result = tool.onPointerUp();

    expect(result.created).toBeUndefined();
    expect(result.updated?.before).toEqual([original]);
    expect(result.updated?.after[0]?.id).toBe(original.id);
  });

  it('hides the original from the scene for the length of the stroke', () => {
    // Without this, the shapes layer would keep painting the *original* mask underneath
    // the shrinking overlay draft, and erasing part of it would look like it did nothing.
    const scene = new Scene({});
    const original = existingMask();
    scene.add(original);
    scene.select([original.id]);

    const tool = new BrushTool();
    tool.onPointerDown(point(15, 15), context(scene));
    expect(scene.get(original.id)).toBeUndefined();

    // `onPointerUp` only *reports* the edit; applying `updated.after` to the scene is
    // `AnnotationEngine.applyResult`'s job (proved separately in engine.test.ts). This is
    // the part of that contract the tool itself owns: the result names the same id that was
    // hidden, so applying it is a plain upsert rather than a resurrection from nowhere.
    const result = tool.onPointerUp();
    expect(result.updated?.after[0]?.id).toBe(original.id);
    scene.update(result.updated!.after[0]!);
    expect(scene.get(original.id)).toBeDefined();
  });

  it('does not edit a mask of a different label', () => {
    const scene = new Scene({});
    const original = existingMask('m1', 'pedestrian');
    scene.add(original);
    scene.select([original.id]);

    const tool = new BrushTool();
    tool.onPointerDown(point(15, 15), context(scene, { labelId: 'car' }));
    const result = tool.onPointerUp();

    expect(result.created).toBeDefined(); // a new mask, not an edit to the pedestrian one
    expect(result.updated).toBeUndefined();
  });

  it('does not edit when more than one thing is selected', () => {
    const scene = new Scene({});
    const first = existingMask('m1');
    const second = existingMask('m2');
    scene.add(first);
    scene.add(second);
    scene.select([first.id, second.id]);

    const tool = new BrushTool();
    tool.onPointerDown(point(15, 15), context(scene));
    const result = tool.onPointerUp();

    expect(result.created).toBeDefined();
    expect(result.updated).toBeUndefined();
  });

  it('grows the mask when painting beyond its original box', () => {
    const scene = new Scene({});
    const original = existingMask(); // a 10x10 block at (10,10)
    scene.add(original);
    scene.select([original.id]);

    const tool = new BrushTool();
    tool.onPointerDown(point(30, 30), context(scene)); // well outside the original block
    const result = tool.onPointerUp();

    expect(maskArea(result.updated?.after[0]?.mask)).toBeGreaterThan(maskArea(original.mask));
    expect(maskContains(result.updated?.after[0]?.mask, 30, 30)).toBe(true);
    expect(maskContains(result.updated?.after[0]?.mask, 12, 12)).toBe(true); // the original survives
  });

  it('shrinks the mask when erasing part of it', () => {
    const scene = new Scene({});
    const original = existingMask(); // a solid 10x10 block
    scene.add(original);
    scene.select([original.id]);

    const tool = new BrushTool();
    tool.onPointerDown(point(11, 11, { altKey: true }), context(scene));
    const result = tool.onPointerUp();

    expect(maskArea(result.updated?.after[0]?.mask)).toBeLessThan(maskArea(original.mask));
  });

  it('deletes the object when a stroke erases every pixel, rather than leaving an empty mask', () => {
    const scene = new Scene({});
    // A tiny mask a single, generously sized erase stroke can cover completely.
    const original = existingMask('m1', 'car', { rle: [0, 4], left: 10, top: 10, width: 2, height: 2 });
    scene.add(original);
    scene.select([original.id]);

    const tool = new BrushTool();
    tool.onPointerDown(point(11, 11, { altKey: true }), context(scene)); // radius 14, covers it all
    const result = tool.onPointerUp();

    expect(result.created).toBeUndefined();
    expect(result.updated).toBeUndefined();
    expect(result.deleted).toEqual([original]);
  });

  it('restores the original on cancel, mid-stroke', () => {
    const scene = new Scene({});
    const original = existingMask();
    scene.add(original);
    scene.select([original.id]);

    const tool = new BrushTool();
    tool.onPointerDown(point(15, 15), context(scene));
    expect(scene.get(original.id)).toBeUndefined();

    const result = tool.cancel();
    expect(scene.get(original.id)).toEqual(original);
    expect(result.draft).toBeNull();
  });

  it('cancel is a no-op when nothing was being edited', () => {
    const tool = new BrushTool();
    expect(() => tool.cancel()).not.toThrow();
  });
});

describe('registration', () => {
  it('is created by name and answers to its shortcut', () => {
    expect(createTool('brush')).toBeInstanceOf(BrushTool);
    expect(TOOL_SHORTCUTS.b).toBe('brush');
  });
});
