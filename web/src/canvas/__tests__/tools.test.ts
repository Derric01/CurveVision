import { beforeEach, describe, expect, it } from 'vitest';
import { Scene } from '../scene';
import {
  EllipseTool,
  PathTool,
  RectangleTool,
  SelectTool,
  TOOL_SHORTCUTS,
  createTool,
  dragHandle,
  findHandle,
  resizeRectangle,
} from '../tools';
import { createViewport } from '../viewport';
import type { Annotation, PointerInput } from '../types';
import type { ToolContext as Context } from '../tools';

function context(scene = new Scene(), labelId: string | null = 'label'): Context {
  return {
    scene,
    viewport: () => ({ ...createViewport(800, 600), scale: 1 }),
    imageSize: () => ({ width: 1000, height: 800 }),
    activeLabelId: () => labelId,
    snapEnabled: () => false,
  };
}

function pointer(x: number, y: number, extra: Partial<PointerInput> = {}): PointerInput {
  return {
    image: { x, y },
    screen: { x, y },
    shiftKey: false,
    ctrlKey: false,
    altKey: false,
    button: 0,
    ...extra,
  };
}

function annotation(id: string, points: number[]): Annotation {
  return {
    id,
    labelId: 'label',
    frame: 0,
    shapeType: 'rectangle',
    points,
    rotation: 0,
    occluded: false,
    outside: false,
    zOrder: 0,
    source: 'manual',
    attributes: {},
  };
}

describe('RectangleTool', () => {
  it('creates a normalised rectangle from a drag', () => {
    const tool = new RectangleTool();
    const ctx = context();

    tool.onPointerDown(pointer(100, 100), ctx);
    tool.onPointerMove(pointer(40, 30), ctx);
    const result = tool.onPointerUp(pointer(40, 30), ctx);

    expect(result.created).toBeDefined();
    // Dragged up and to the left; the stored rectangle is still ordered.
    expect(result.created!.points).toEqual([40, 30, 100, 100]);
    expect(result.draft).toBeNull();
  });

  it('reports a draft while dragging', () => {
    const tool = new RectangleTool();
    const ctx = context();
    tool.onPointerDown(pointer(10, 10), ctx);
    const moving = tool.onPointerMove(pointer(60, 40), ctx);
    expect(moving.draft?.points).toEqual([10, 10, 60, 40]);
  });

  it('discards a degenerate click rather than creating an invisible object', () => {
    const tool = new RectangleTool();
    const ctx = context();
    tool.onPointerDown(pointer(50, 50), ctx);
    tool.onPointerMove(pointer(50, 51), ctx);
    expect(tool.onPointerUp(pointer(50, 51), ctx).created).toBeUndefined();
  });

  it('does nothing without an active label', () => {
    const tool = new RectangleTool();
    const ctx = context(new Scene(), null);
    expect(tool.onPointerDown(pointer(0, 0), ctx).draft).toBeUndefined();
  });

  it('clamps the rectangle to the frame', () => {
    const tool = new RectangleTool();
    const ctx = context();
    tool.onPointerDown(pointer(900, 700), ctx);
    tool.onPointerMove(pointer(5000, 5000), ctx);
    const result = tool.onPointerUp(pointer(5000, 5000), ctx);
    expect(result.created!.points).toEqual([900, 700, 1000, 800]);
  });
});

describe('EllipseTool', () => {
  it('draws from the centre outwards', () => {
    const tool = new EllipseTool();
    const ctx = context();
    tool.onPointerDown(pointer(100, 100), ctx);
    tool.onPointerMove(pointer(150, 130), ctx);
    const result = tool.onPointerUp();

    expect(result.created!.points).toEqual([100, 100, 50, 30]);
  });

  it('discards an ellipse with no radius', () => {
    const tool = new EllipseTool();
    const ctx = context();
    tool.onPointerDown(pointer(100, 100), ctx);
    expect(tool.onPointerUp().created).toBeUndefined();
  });
});

describe('PathTool', () => {
  let tool: PathTool;
  let ctx: Context;

  beforeEach(() => {
    tool = new PathTool('polygon');
    ctx = context();
  });

  it('accumulates vertices on each click', () => {
    tool.onPointerDown(pointer(0, 0), ctx);
    tool.onPointerDown(pointer(100, 0), ctx);
    tool.onPointerDown(pointer(50, 80), ctx);
    expect(tool.vertexCount).toBe(3);
  });

  it('commits on Enter once it has enough vertices', () => {
    tool.onPointerDown(pointer(0, 0), ctx);
    tool.onPointerDown(pointer(100, 0), ctx);
    tool.onPointerDown(pointer(50, 80), ctx);

    const result = tool.onKey('Enter', ctx);
    expect(result?.created?.points).toEqual([0, 0, 100, 0, 50, 80]);
    expect(tool.vertexCount).toBe(0);
  });

  it('refuses to commit a polygon with too few vertices', () => {
    tool.onPointerDown(pointer(0, 0), ctx);
    tool.onPointerDown(pointer(100, 0), ctx);

    const result = tool.onKey('Enter', ctx);
    expect(result?.created).toBeUndefined();
    expect(tool.vertexCount).toBe(0);
  });

  it('removes the last vertex on Backspace', () => {
    tool.onPointerDown(pointer(0, 0), ctx);
    tool.onPointerDown(pointer(100, 0), ctx);
    tool.onKey('Backspace', ctx);
    expect(tool.vertexCount).toBe(1);
  });

  it('abandons the shape on Escape', () => {
    tool.onPointerDown(pointer(0, 0), ctx);
    tool.onPointerDown(pointer(100, 0), ctx);
    const result = tool.onKey('Escape', ctx);
    expect(result?.draft).toBeNull();
    expect(tool.vertexCount).toBe(0);
  });

  it('shows the segment the next click would commit', () => {
    tool.onPointerDown(pointer(0, 0), ctx);
    const preview = tool.onPointerMove(pointer(70, 20), ctx);
    expect(preview.draft?.points).toEqual([0, 0, 70, 20]);
  });

  it('completes a points annotation on the first click', () => {
    const points = new PathTool('points');
    const result = points.onPointerDown(pointer(30, 40), ctx);
    expect(result.created?.points).toEqual([30, 40]);
  });

  it('accepts a two-vertex polyline', () => {
    const polyline = new PathTool('polyline');
    polyline.onPointerDown(pointer(0, 0), ctx);
    polyline.onPointerDown(pointer(50, 50), ctx);
    expect(polyline.onKey('Enter', ctx)?.created).toBeDefined();
  });

  it('snaps to a nearby vertex when snapping is on', () => {
    const scene = new Scene();
    scene.load([annotation('neighbour', [200, 200, 260, 260])]);
    const snapping: Context = { ...context(scene), snapEnabled: () => true };

    const result = new PathTool('polygon').onPointerDown(pointer(202, 201), snapping);
    expect(result.snap).toEqual({ x: 200, y: 200 });
    expect(result.draft?.points).toEqual([200, 200]);
  });
});

describe('SelectTool', () => {
  it('selects the shape under the pointer', () => {
    const scene = new Scene();
    scene.load([annotation('a', [0, 0, 100, 100])]);
    const ctx = context(scene);

    new SelectTool().onPointerDown(pointer(50, 50), ctx);
    expect(scene.selectedIds).toEqual(['a']);
  });

  it('adds to the selection with shift', () => {
    const scene = new Scene();
    scene.load([annotation('a', [0, 0, 50, 50]), annotation('b', [200, 200, 250, 250])]);
    const ctx = context(scene);
    const tool = new SelectTool();

    tool.onPointerDown(pointer(25, 25), ctx);
    tool.onPointerUp(pointer(25, 25), ctx);
    tool.onPointerDown(pointer(225, 225, { shiftKey: true }), ctx);

    expect(scene.selectedIds.sort()).toEqual(['a', 'b']);
  });

  it('clears the selection when clicking empty space', () => {
    const scene = new Scene();
    scene.load([annotation('a', [0, 0, 50, 50])]);
    const ctx = context(scene);
    const tool = new SelectTool();

    tool.onPointerDown(pointer(25, 25), ctx);
    tool.onPointerUp(pointer(25, 25), ctx);
    tool.onPointerDown(pointer(500, 500), ctx);

    expect(scene.selectedIds).toEqual([]);
  });

  it('moves the selection by the pointer delta', () => {
    const scene = new Scene();
    scene.load([annotation('a', [0, 0, 50, 50])]);
    const ctx = context(scene);
    const tool = new SelectTool();

    tool.onPointerDown(pointer(25, 25), ctx);
    const result = tool.onPointerMove(pointer(35, 45), ctx);
    tool.onPointerUp(pointer(35, 45), ctx);

    expect(scene.get('a')!.points).toEqual([10, 20, 60, 70]);
    expect(result.updated?.before[0]!.points).toEqual([0, 0, 50, 50]);
  });

  it('rubber-band selects everything inside the marquee', () => {
    const scene = new Scene();
    scene.load([
      annotation('a', [10, 10, 40, 40]),
      annotation('b', [60, 60, 90, 90]),
      annotation('c', [500, 500, 540, 540]),
    ]);
    const ctx = context(scene);
    const tool = new SelectTool();

    tool.onPointerDown(pointer(0, 0), ctx);
    tool.onPointerMove(pointer(100, 100), ctx);
    tool.onPointerUp(pointer(100, 100), ctx);

    expect(scene.selectedIds.sort()).toEqual(['a', 'b']);
  });

  it('drags a rectangle corner without breaking axis alignment', () => {
    const scene = new Scene();
    scene.load([annotation('a', [0, 0, 100, 100])]);
    const ctx = context(scene);
    const tool = new SelectTool();

    scene.select(['a']);
    tool.onPointerDown(pointer(100, 100), ctx); // bottom-right handle
    tool.onPointerMove(pointer(160, 130), ctx);

    expect(scene.get('a')!.points).toEqual([0, 0, 160, 130]);
  });

  it('does not select a shape whose label is locked', () => {
    const scene = new Scene();
    scene.setLabels([{ id: 'label', name: 'car', color: '#fff', visible: true, locked: true }]);
    scene.load([annotation('a', [0, 0, 100, 100])]);

    new SelectTool().onPointerDown(pointer(50, 50), context(scene));
    expect(scene.selectedIds).toEqual([]);
  });
});

describe('resizeRectangle', () => {
  it('anchors the opposite corner', () => {
    expect(resizeRectangle([0, 0, 100, 100], 2, { x: 150, y: 120 })).toEqual([0, 0, 150, 120]);
    expect(resizeRectangle([0, 0, 100, 100], 0, { x: 20, y: 30 })).toEqual([20, 30, 100, 100]);
  });
});

describe('createTool', () => {
  it('builds each named tool', () => {
    expect(createTool('rectangle').name).toBe('rectangle');
    expect(createTool('polygon').name).toBe('polygon');
    expect(createTool('ellipse').name).toBe('ellipse');
    expect(createTool('pan').name).toBe('pan');
    expect(createTool('select').name).toBe('select');
    expect(createTool('cuboid').name).toBe('cuboid');
  });

  it('maps single-key shortcuts to tools', () => {
    expect(TOOL_SHORTCUTS.r).toBe('rectangle');
    expect(TOOL_SHORTCUTS.v).toBe('select');
    expect(TOOL_SHORTCUTS.p).toBe('polygon');
    expect(TOOL_SHORTCUTS.c).toBe('cuboid');
  });
});

describe('handle dragging', () => {
  it('finds the visible corner handles of a rectangle, not its two stored points', () => {
    const rect = annotation('a', [0, 0, 100, 100]);
    // The renderer draws four corners; all four must be grabbable.
    expect(findHandle(rect, { x: 100, y: 0 }, 4)).toBe(1);
    expect(findHandle(rect, { x: 0, y: 100 }, 4)).toBe(3);
    expect(findHandle(rect, { x: 50, y: 50 }, 4)).toBe(-1);
  });

  it('resizes an ellipse by its radius handles without moving the centre', () => {
    const ellipse: Annotation = { ...annotation('e', [100, 100, 40, 20]), shapeType: 'ellipse' };
    expect(dragHandle(ellipse, 0, { x: 160, y: 100 })).toEqual([100, 100, 60, 20]);
    expect(dragHandle(ellipse, 1, { x: 100, y: 135 })).toEqual([100, 100, 40, 35]);
  });

  it('moves a single polygon vertex', () => {
    const polygon: Annotation = {
      ...annotation('p', [0, 0, 100, 0, 50, 80]),
      shapeType: 'polygon',
    };
    expect(dragHandle(polygon, 1, { x: 120, y: 10 })).toEqual([0, 0, 120, 10, 50, 80]);
  });
});
