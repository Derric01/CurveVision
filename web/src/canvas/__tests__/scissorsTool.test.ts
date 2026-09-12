/**
 * The scissors tool's state machine.
 *
 * `scissors.test.ts` proves the wire follows edges. This proves the tool around it: that a
 * click anchors, that the preview updates without committing, that Backspace unwinds exactly
 * one anchor, and — the one that matters most in practice — that the tool refuses to run
 * rather than quietly degrading into a straight-line polygon when it cannot read pixels.
 */

import { describe, expect, it } from 'vitest';

import { Scene } from '../scene';
import { createTool, ScissorsTool, TOOL_SHORTCUTS, type ToolContext } from '../tools';
import { createViewport } from '../viewport';
import type { PointerInput } from '../types';

/** A dark band bulging upward, so the correct answer is visibly not a straight line. */
function bandImage(width = 160, height = 100): ImageData {
  const edgeY = (x: number): number => Math.round(70 - 40 * Math.sin((Math.PI * x) / (width - 1)));
  const data = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const value = Math.abs(y - edgeY(x)) <= 1 ? 0 : 255;
      const index = (y * width + x) * 4;
      data[index] = data[index + 1] = data[index + 2] = value;
      data[index + 3] = 255;
    }
  }
  return { data, width, height, colorSpace: 'srgb' } as ImageData;
}

function edgeYOf(width = 160): (x: number) => number {
  return (x) => Math.round(70 - 40 * Math.sin((Math.PI * x) / (width - 1)));
}

function context(image: ImageData | null, labelId: string | null = 'label-1'): ToolContext {
  return {
    scene: new Scene({}),
    viewport: () => createViewport(),
    imageSize: () => ({ width: image?.width ?? 0, height: image?.height ?? 0 }),
    activeLabelId: () => labelId,
    snapEnabled: () => false,
    imageData: () => image,
  };
}

/**
 * Where the polygon crosses a vertical line, by interpolating along it.
 *
 * Asking for a *vertex* near some x is the wrong question after simplification: a smooth
 * curve is deliberately left with few vertices, so the crest of an arc often has none. What
 * the test means to ask is where the boundary passes, which is a segment crossing.
 */
function yWhereCrossing(points: number[], targetX: number): number | null {
  for (let i = 0; i + 3 < points.length; i += 2) {
    const x0 = points[i]!;
    const y0 = points[i + 1]!;
    const x1 = points[i + 2]!;
    const y1 = points[i + 3]!;
    if ((x0 - targetX) * (x1 - targetX) <= 0 && x0 !== x1) {
      return y0 + ((targetX - x0) / (x1 - x0)) * (y1 - y0);
    }
  }
  return null;
}

function at(x: number, y: number): PointerInput {
  return {
    image: { x, y },
    screen: { x, y },
    shiftKey: false,
    ctrlKey: false,
    altKey: false,
    button: 0,
  } as PointerInput;
}

describe('registration', () => {
  it('is reachable by name and by keyboard', () => {
    expect(createTool('scissors')).toBeInstanceOf(ScissorsTool);
    expect(TOOL_SHORTCUTS.s).toBe('scissors');
  });

  it('produces polygons', () => {
    const image = bandImage();
    const edgeY = edgeYOf();
    const tool = new ScissorsTool();
    const ctx = context(image);

    tool.onPointerDown(at(8, edgeY(8)), ctx);
    tool.onPointerDown(at(150, edgeY(150)), ctx);
    const result = tool.finish();

    expect(result.created?.shapeType).toBe('polygon');
    expect(result.created?.labelId).toBe('label-1');
  });
});

describe('drawing', () => {
  it('the committed boundary follows the edge, not the straight line between clicks', () => {
    const image = bandImage();
    const edgeY = edgeYOf();
    const tool = new ScissorsTool();
    const ctx = context(image);

    tool.onPointerDown(at(8, edgeY(8)), ctx);
    tool.onPointerDown(at(150, edgeY(150)), ctx);
    const created = tool.finish().created;

    expect(created).toBeDefined();
    const points = created!.points;
    expect(points.length / 2).toBeGreaterThan(3);

    // Near the crest the band sits at y≈30; the chord between the two clicks sits at y≈70.
    const crest = yWhereCrossing(points, 80);
    expect(crest).not.toBeNull();
    expect(crest!).toBeLessThan(45);
  });

  it('thins the pixel-dense wire into an editable polygon', () => {
    const image = bandImage();
    const edgeY = edgeYOf();
    const tool = new ScissorsTool();
    const ctx = context(image);

    tool.onPointerDown(at(8, edgeY(8)), ctx);
    tool.onPointerDown(at(150, edgeY(150)), ctx);
    const created = tool.finish().created!;

    // The raw wire is one vertex per pixel — well over a hundred here. A polygon that dense
    // is unusable: slow to render, impossible to edit by hand, and huge in an export.
    expect(created.points.length / 2).toBeLessThan(40);
  });

  it('previews on move without committing anything', () => {
    const image = bandImage();
    const edgeY = edgeYOf();
    const tool = new ScissorsTool();
    const ctx = context(image);

    tool.onPointerDown(at(8, edgeY(8)), ctx);
    const before = tool.vertexCount;
    const moved = tool.onPointerMove(at(120, edgeY(120)), ctx);

    expect(moved.draft).toBeTruthy();
    expect(moved.draft!.points.length).toBeGreaterThan(4);
    expect(moved.created).toBeUndefined();
    expect(tool.vertexCount).toBe(before);
  });

  it('does nothing before the first anchor', () => {
    const tool = new ScissorsTool();
    expect(tool.onPointerMove(at(10, 10), context(bandImage())).draft).toBeUndefined();
  });
});

describe('unwinding', () => {
  it('Backspace removes exactly one anchor and its segment', () => {
    const image = bandImage();
    const edgeY = edgeYOf();
    const tool = new ScissorsTool();
    const ctx = context(image);

    tool.onPointerDown(at(8, edgeY(8)), ctx);
    tool.onPointerDown(at(80, edgeY(80)), ctx);
    const afterTwo = tool.vertexCount;
    tool.onPointerDown(at(150, edgeY(150)), ctx);
    expect(tool.vertexCount).toBeGreaterThan(afterTwo);

    tool.onKey('Backspace', ctx);
    expect(tool.vertexCount).toBe(afterTwo);
  });

  it('Backspace past the first anchor abandons the shape', () => {
    const image = bandImage();
    const edgeY = edgeYOf();
    const tool = new ScissorsTool();
    const ctx = context(image);

    tool.onPointerDown(at(8, edgeY(8)), ctx);
    const result = tool.onKey('Backspace', ctx);

    expect(result?.draft).toBeNull();
    expect(tool.vertexCount).toBe(0);
  });

  it('Escape abandons without producing a shape', () => {
    const image = bandImage();
    const edgeY = edgeYOf();
    const tool = new ScissorsTool();
    const ctx = context(image);

    tool.onPointerDown(at(8, edgeY(8)), ctx);
    tool.onPointerDown(at(150, edgeY(150)), ctx);
    const result = tool.onKey('Escape', ctx);

    expect(result?.created).toBeUndefined();
    expect(tool.vertexCount).toBe(0);
  });

  it('refuses to finish a polygon with too few vertices', () => {
    const tool = new ScissorsTool();
    const ctx = context(bandImage());
    tool.onPointerDown(at(8, 70), ctx);
    // One anchor is a point, not a polygon.
    expect(tool.finish().created).toBeUndefined();
  });
});

describe('when it cannot run', () => {
  it('declines rather than drawing straight lines when there are no pixels', () => {
    // The important failure mode. Falling back to straight segments would look exactly like
    // the tool working badly — the annotator would blame the algorithm and keep clicking —
    // where doing nothing is at least legible.
    const tool = new ScissorsTool();
    const ctx = context(null);

    const result = tool.onPointerDown(at(10, 10), ctx);

    expect(result.draft).toBeUndefined();
    expect(tool.vertexCount).toBe(0);
  });

  it('declines when no label is selected, like every other drawing tool', () => {
    const tool = new ScissorsTool();
    const result = tool.onPointerDown(at(10, 10), context(bandImage(), null));

    expect(result.draft).toBeUndefined();
    expect(tool.vertexCount).toBe(0);
  });

  it('recomputes its edge map when the frame changes', () => {
    const tool = new ScissorsTool();
    const edgeY = edgeYOf();
    let image = bandImage();
    const ctx: ToolContext = { ...context(image), imageData: () => image };

    tool.onPointerDown(at(8, edgeY(8)), ctx);
    tool.onKey('Escape', ctx);

    // A different frame: a band bulging *downward* instead. If the features were stale the
    // wire would follow the old edge, which is the bug this guards.
    const width = 160;
    const height = 100;
    const flipped = new Uint8ClampedArray(width * height * 4);
    const lowEdge = (x: number): number => Math.round(30 + 40 * Math.sin((Math.PI * x) / (width - 1)));
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        const value = Math.abs(y - lowEdge(x)) <= 1 ? 0 : 255;
        const index = (y * width + x) * 4;
        flipped[index] = flipped[index + 1] = flipped[index + 2] = value;
        flipped[index + 3] = 255;
      }
    }
    image = { data: flipped, width, height, colorSpace: 'srgb' } as ImageData;

    tool.onPointerDown(at(8, lowEdge(8)), ctx);
    tool.onPointerDown(at(150, lowEdge(150)), ctx);
    const created = tool.finish().created!;

    const middle = yWhereCrossing(created.points, 80);
    expect(middle).not.toBeNull();
    // The new band dips to y≈70; the old one crested at y≈30.
    expect(middle!).toBeGreaterThan(55);
  });
});
