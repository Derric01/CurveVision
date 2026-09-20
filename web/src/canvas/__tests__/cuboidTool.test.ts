/**
 * The cuboid tool's two-stage state machine: drag the front face, then move and click to
 * set the depth. `geometry.test.ts` proves `cuboidFromFrontFace` itself; this proves the
 * gesture around it -- that a click before the front face is dragged out does nothing, that
 * a degenerate front face is discarded rather than committed, that a depth click too close to
 * where the front face ended is ignored rather than creating a flat box, and that Escape
 * abandons whichever stage is in progress.
 */

import { describe, expect, it } from 'vitest';
import { cuboidFromFrontFace } from '../geometry';
import { Scene } from '../scene';
import { CuboidTool, type ToolContext } from '../tools';
import { createViewport } from '../viewport';
import type { PointerInput } from '../types';

function context(overrides: { labelId?: string | null; width?: number; height?: number } = {}): ToolContext {
  const { labelId = 'car', width = 200, height = 200 } = overrides;
  return {
    scene: new Scene({}),
    viewport: () => createViewport(),
    imageSize: () => ({ width, height }),
    activeLabelId: () => labelId,
    snapEnabled: () => false,
  };
}

function point(x: number, y: number): PointerInput {
  return { image: { x, y }, screen: { x, y }, shiftKey: false, ctrlKey: false, altKey: false, button: 0 };
}

describe('drawing the front face', () => {
  it('refuses to start without an active label', () => {
    const tool = new CuboidTool();
    const result = tool.onPointerDown(point(10, 10), context({ labelId: null }));
    expect(result).toEqual({});
    expect(tool.drawing).toBe('idle');
  });

  it('previews the front face as a flat cuboid while dragging', () => {
    const tool = new CuboidTool();
    tool.onPointerDown(point(10, 10), context());
    const result = tool.onPointerMove(point(50, 40), context());
    expect(result.draft?.shapeType).toBe('cuboid');
    expect(result.draft?.points).toEqual(cuboidFromFrontFace([10, 10, 50, 40], 0, 0));
    expect(tool.drawing).toBe('front');
  });

  it('discards a click that never dragged out a real front face', () => {
    const tool = new CuboidTool();
    tool.onPointerDown(point(10, 10), context());
    const result = tool.onPointerUp(point(11, 10), context());
    expect(result).toEqual({ draft: null, status: null });
    expect(tool.drawing).toBe('idle');
  });

  it('moves to the depth stage once a real front face is dragged out', () => {
    const tool = new CuboidTool();
    tool.onPointerDown(point(10, 10), context());
    const result = tool.onPointerUp(point(60, 50), context());
    expect(result.status).toContain('depth');
    expect(tool.drawing).toBe('depth');
  });
});

describe('setting the depth', () => {
  function afterFrontFace(): { tool: CuboidTool; ctx: ToolContext } {
    const tool = new CuboidTool();
    const ctx = context();
    tool.onPointerDown(point(10, 10), ctx);
    tool.onPointerUp(point(60, 50), ctx);
    return { tool, ctx };
  }

  it('previews the back face following the pointer', () => {
    const { tool, ctx } = afterFrontFace();
    const result = tool.onPointerMove(point(80, 70), ctx); // (dx, dy) = (20, 20) from (60, 50)
    expect(result.draft?.points).toEqual(cuboidFromFrontFace([10, 10, 60, 50], 20, 20));
  });

  it('ignores a click before the pointer has moved enough to mean a real depth', () => {
    const { tool, ctx } = afterFrontFace();
    const result = tool.onPointerDown(point(61, 50), ctx); // 1px from the depth anchor
    expect(result).toEqual({});
    expect(tool.drawing).toBe('depth'); // still waiting, not cancelled
  });

  it('commits an 8-point cuboid on a click with real depth', () => {
    const { tool, ctx } = afterFrontFace();
    const result = tool.onPointerDown(point(80, 70), ctx); // (dx, dy) = (20, 20)
    expect(result.created?.shapeType).toBe('cuboid');
    expect(result.created?.points).toEqual(cuboidFromFrontFace([10, 10, 60, 50], 20, 20));
    expect(result.draft).toBeNull();
    expect(tool.drawing).toBe('idle');
  });

  it('clamps the committed cuboid into the frame', () => {
    const { tool, ctx } = afterFrontFace();
    const result = tool.onPointerDown(point(300, 30), ctx); // far past the 200px-wide frame
    const points = result.created!.points;
    expect(Math.max(...points.filter((_, i) => i % 2 === 0))).toBeLessThanOrEqual(200);
  });
});

describe('cancelling', () => {
  it('abandons a front-face drag in progress', () => {
    const tool = new CuboidTool();
    tool.onPointerDown(point(10, 10), context());
    const result = tool.onKey('Escape');
    expect(result).toEqual({ draft: null, status: null });
    expect(tool.drawing).toBe('idle');
  });

  it('abandons the depth stage, discarding the front face too', () => {
    const tool = new CuboidTool();
    const ctx = context();
    tool.onPointerDown(point(10, 10), ctx);
    tool.onPointerUp(point(60, 50), ctx);
    const result = tool.onKey('Escape');
    expect(result).toEqual({ draft: null, status: null });
    expect(tool.drawing).toBe('idle');

    // A fresh drag afterward starts over rather than resuming the abandoned one.
    tool.onPointerDown(point(0, 0), ctx);
    const preview = tool.onPointerMove(point(20, 20), ctx);
    expect(preview.draft?.points).toEqual(cuboidFromFrontFace([0, 0, 20, 20], 0, 0));
  });

  it('ignores keys it does not handle', () => {
    const tool = new CuboidTool();
    expect(tool.onKey('a')).toBeNull();
  });
});
