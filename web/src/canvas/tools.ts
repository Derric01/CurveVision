/**
 * Tools: one small state machine per interaction.
 *
 * Each tool owns its own transient state and reports what it produced. The engine routes
 * pointer events to the active tool and knows nothing about how any individual tool works,
 * which is what stops the editor collecting a `if (mode === ...)` cascade.
 */

import {
  boundsOf,
  clampToImage,
  distance,
  normalizeRectangle,
  snapToVertex,
  translatePoints,
} from './geometry';
import { handlePoints } from './renderer';
import type { Scene } from './scene';
import { screenTolerance } from './viewport';
import type {
  Annotation,
  Box,
  Point,
  PointerInput,
  ShapeType,
  ToolName,
  ViewportState,
} from './types';
import { MIN_VERTICES } from './types';

export interface ToolContext {
  scene: Scene;
  viewport: () => ViewportState;
  imageSize: () => { width: number; height: number };
  /** Label to apply to newly created objects. */
  activeLabelId: () => string | null;
  /** Snapping to nearby vertices, so adjacent polygons share a boundary exactly. */
  snapEnabled: () => boolean;
}

export interface ToolResult {
  /** Object finished and ready to be committed. */
  created?: Annotation;
  /** Objects whose geometry changed, with their pre-edit state for undo. */
  updated?: { before: Annotation[]; after: Annotation[] };
  /** The object being drawn right now, for the overlay. */
  draft?: Annotation | null;
  marquee?: Box | null;
  /** Vertex the pointer snapped to, for the overlay. */
  snap?: Point | null;
  /** Redraw the shape layer, not just the overlay. */
  invalidateShapes?: boolean;
}

export interface Tool {
  readonly name: ToolName;
  /** Cursor to show while this tool is active. */
  readonly cursor: string;
  onPointerDown(input: PointerInput, context: ToolContext): ToolResult;
  onPointerMove(input: PointerInput, context: ToolContext): ToolResult;
  onPointerUp(input: PointerInput, context: ToolContext): ToolResult;
  /** Enter, Escape, Backspace. Returns a result, or null if the key is not handled. */
  onKey?(key: string, context: ToolContext): ToolResult | null;
  /** Discard transient state, e.g. when switching tools mid-draw. */
  cancel(): ToolResult;
}

let clientCounter = 0;

export function nextClientId(): string {
  clientCounter += 1;
  return `local-${Date.now().toString(36)}-${clientCounter}`;
}

function draftAnnotation(
  labelId: string,
  shapeType: ShapeType,
  points: number[],
): Annotation {
  return {
    id: nextClientId(),
    clientId: nextClientId(),
    labelId,
    frame: 0,
    shapeType,
    points,
    rotation: 0,
    occluded: false,
    outside: false,
    zOrder: 0,
    source: 'manual',
    attributes: {},
    pending: true,
  };
}

// --------------------------------------------------------------------------- select

type DragMode = 'none' | 'move' | 'vertex' | 'marquee';

/**
 * Selection, moving and vertex editing.
 *
 * The default tool, and the one whose feel matters most: a click that misses by two pixels
 * or a drag that starts from the wrong object is the difference between a pleasant tool and
 * an exhausting one.
 */
export class SelectTool implements Tool {
  readonly name = 'select' as const;
  readonly cursor = 'default';

  private mode: DragMode = 'none';
  private start: Point | null = null;
  private last: Point | null = null;
  private before: Annotation[] = [];
  private vertexTarget: { id: string; index: number } | null = null;
  private marqueeAdditive = false;

  onPointerDown(input: PointerInput, context: ToolContext): ToolResult {
    const viewport = context.viewport();
    const tolerance = screenTolerance(viewport);
    this.start = input.image;
    this.last = input.image;

    // A handle on an already-selected object takes priority over picking a new object,
    // so grabbing a corner never accidentally selects whatever is behind it. Handles are
    // located with the same function the renderer draws them from -- indexing into the raw
    // coordinate list instead would silently disagree for rectangles and ellipses, whose
    // four visible handles are derived rather than stored.
    for (const annotation of context.scene.selected) {
      const index = findHandle(annotation, input.image, tolerance * 1.5);
      if (index >= 0) {
        this.mode = 'vertex';
        this.vertexTarget = { id: annotation.id, index };
        this.before = [{ ...annotation }];
        return {};
      }
    }

    const hit = context.scene.pick(input.image, tolerance);
    if (hit) {
      if (input.shiftKey) {
        context.scene.toggleSelection(hit.id);
      } else if (!context.scene.isSelected(hit.id)) {
        context.scene.select([hit.id]);
      }
      this.mode = 'move';
      this.before = context.scene.selected.map((a) => ({ ...a }));
      return { invalidateShapes: true };
    }

    if (!input.shiftKey) context.scene.clearSelection();
    this.mode = 'marquee';
    this.marqueeAdditive = input.shiftKey;
    return { marquee: { minX: input.image.x, minY: input.image.y, maxX: input.image.x, maxY: input.image.y }, invalidateShapes: true };
  }

  onPointerMove(input: PointerInput, context: ToolContext): ToolResult {
    if (this.mode === 'none') {
      const tolerance = screenTolerance(context.viewport());
      const hit = context.scene.pick(input.image, tolerance);
      const changed = context.scene.setHovered(hit?.id ?? null);
      return changed ? {} : {};
    }

    if (this.mode === 'marquee' && this.start) {
      return {
        marquee: {
          minX: Math.min(this.start.x, input.image.x),
          minY: Math.min(this.start.y, input.image.y),
          maxX: Math.max(this.start.x, input.image.x),
          maxY: Math.max(this.start.y, input.image.y),
        },
      };
    }

    if (!this.last) return {};
    const dx = input.image.x - this.last.x;
    const dy = input.image.y - this.last.y;
    this.last = input.image;
    const { width, height } = context.imageSize();

    if (this.mode === 'move') {
      const after: Annotation[] = [];
      for (const annotation of context.scene.selected) {
        const moved: Annotation = {
          ...annotation,
          points: clampToImage(translatePoints(annotation.points, dx, dy), width, height),
        };
        context.scene.update(moved);
        after.push(moved);
      }
      return { updated: { before: this.before, after }, invalidateShapes: true };
    }

    if (this.mode === 'vertex' && this.vertexTarget) {
      const annotation = context.scene.get(this.vertexTarget.id);
      if (!annotation) return {};

      const points = dragHandle(annotation, this.vertexTarget.index, input.image);
      const updated = { ...annotation, points: clampToImage(points, width, height) };
      context.scene.update(updated);
      return { updated: { before: this.before, after: [updated] }, invalidateShapes: true };
    }

    return {};
  }

  onPointerUp(input: PointerInput, context: ToolContext): ToolResult {
    const result: ToolResult = { marquee: null, invalidateShapes: true };

    if (this.mode === 'marquee' && this.start) {
      const box: Box = {
        minX: Math.min(this.start.x, input.image.x),
        minY: Math.min(this.start.y, input.image.y),
        maxX: Math.max(this.start.x, input.image.x),
        maxY: Math.max(this.start.y, input.image.y),
      };
      // A stray click reads as a marquee of zero size; treat only a real drag as one.
      if (box.maxX - box.minX > 1 || box.maxY - box.minY > 1) {
        const ids = context.scene.pickInBox(box).map((a) => a.id);
        context.scene.select(ids, this.marqueeAdditive);
      }
    }

    this.mode = 'none';
    this.start = null;
    this.last = null;
    this.before = [];
    this.vertexTarget = null;
    return result;
  }

  cancel(): ToolResult {
    this.mode = 'none';
    this.start = null;
    this.last = null;
    this.before = [];
    this.vertexTarget = null;
    return { marquee: null };
  }
}

/**
 * Index of the drag handle within `tolerance` of `point`, or -1.
 *
 * Uses the renderer's handle positions rather than the raw coordinate list, so what the
 * annotator can grab is exactly what they can see.
 */
export function findHandle(annotation: Annotation, point: Point, tolerance: number): number {
  const handles = handlePoints(annotation);
  for (let i = 0; i < handles.length; i++) {
    if (distance(point, handles[i]!) <= tolerance) return i;
  }
  return -1;
}

/** Apply a handle drag, respecting each shape's semantics. */
export function dragHandle(annotation: Annotation, handle: number, to: Point): number[] {
  const { shapeType, points } = annotation;

  if (shapeType === 'rectangle' || shapeType === 'rotated_rectangle' || shapeType === 'mask') {
    // A rectangle's corner drag anchors the opposite corner, so the box stays
    // axis-aligned rather than degenerating into a quadrilateral.
    return resizeRectangle(points, handle, to);
  }

  if (shapeType === 'ellipse') {
    // Handle 0 sets the horizontal radius, handle 1 the vertical; the centre stays put.
    const [cx = 0, cy = 0, rx = 0, ry = 0] = points;
    return handle === 0
      ? [cx, cy, Math.abs(to.x - cx), ry]
      : [cx, cy, rx, Math.abs(to.y - cy)];
  }

  const moved = [...points];
  const offset = handle * 2;
  if (offset + 1 < moved.length) {
    moved[offset] = to.x;
    moved[offset + 1] = to.y;
  }
  return moved;
}

/** Move one corner of an axis-aligned rectangle, keeping it axis-aligned. */
export function resizeRectangle(points: number[], corner: number, to: Point): number[] {
  const [minX = 0, minY = 0, maxX = 0, maxY = 0] = normalizeRectangle(points);
  const corners: [number, number][] = [
    [minX, minY],
    [maxX, minY],
    [maxX, maxY],
    [minX, maxY],
  ];
  const opposite = corners[(corner + 2) % 4]!;
  return normalizeRectangle([opposite[0], opposite[1], to.x, to.y]);
}

// ------------------------------------------------------------------------ rectangle

export class RectangleTool implements Tool {
  readonly name = 'rectangle' as const;
  readonly cursor = 'crosshair';

  private origin: Point | null = null;
  private draft: Annotation | null = null;

  onPointerDown(input: PointerInput, context: ToolContext): ToolResult {
    const labelId = context.activeLabelId();
    if (!labelId) return {};
    this.origin = input.image;
    this.draft = draftAnnotation(labelId, 'rectangle', [
      input.image.x,
      input.image.y,
      input.image.x,
      input.image.y,
    ]);
    return { draft: this.draft };
  }

  onPointerMove(input: PointerInput, context: ToolContext): ToolResult {
    if (!this.origin || !this.draft) return {};
    const { width, height } = context.imageSize();
    this.draft = {
      ...this.draft,
      points: clampToImage(
        normalizeRectangle([this.origin.x, this.origin.y, input.image.x, input.image.y]),
        width,
        height,
      ),
    };
    return { draft: this.draft };
  }

  onPointerUp(_input: PointerInput, _context: ToolContext): ToolResult {
    const draft = this.draft;
    this.origin = null;
    this.draft = null;
    if (!draft) return { draft: null };

    const [x1 = 0, y1 = 0, x2 = 0, y2 = 0] = draft.points;
    // Reject a click that produced a degenerate box rather than creating an invisible
    // object the annotator then has to hunt down and delete.
    if (Math.abs(x2 - x1) < 2 || Math.abs(y2 - y1) < 2) return { draft: null };
    return { created: draft, draft: null };
  }

  cancel(): ToolResult {
    this.origin = null;
    this.draft = null;
    return { draft: null };
  }
}

// -------------------------------------------------------------------------- ellipse

export class EllipseTool implements Tool {
  readonly name = 'ellipse' as const;
  readonly cursor = 'crosshair';

  private origin: Point | null = null;
  private draft: Annotation | null = null;

  onPointerDown(input: PointerInput, context: ToolContext): ToolResult {
    const labelId = context.activeLabelId();
    if (!labelId) return {};
    this.origin = input.image;
    this.draft = draftAnnotation(labelId, 'ellipse', [input.image.x, input.image.y, 0, 0]);
    return { draft: this.draft };
  }

  onPointerMove(input: PointerInput, _context: ToolContext): ToolResult {
    if (!this.origin || !this.draft) return {};
    // Drag from the centre outwards: for an ellipse that is the interaction people expect,
    // unlike a rectangle where corner-to-corner is the norm.
    this.draft = {
      ...this.draft,
      points: [
        this.origin.x,
        this.origin.y,
        Math.abs(input.image.x - this.origin.x),
        Math.abs(input.image.y - this.origin.y),
      ],
    };
    return { draft: this.draft };
  }

  onPointerUp(): ToolResult {
    const draft = this.draft;
    this.origin = null;
    this.draft = null;
    if (!draft) return { draft: null };
    const [, , rx = 0, ry = 0] = draft.points;
    if (rx < 2 || ry < 2) return { draft: null };
    return { created: draft, draft: null };
  }

  cancel(): ToolResult {
    this.origin = null;
    this.draft = null;
    return { draft: null };
  }
}

// -------------------------------------------------------- polygon / polyline / points

/**
 * Multi-vertex drawing.
 *
 * Click adds a vertex, Enter or a double-click finishes, Backspace removes the last one,
 * Escape abandons the shape. That set of bindings is what annotators coming from any other
 * tool in this space already have in their fingers.
 */
export class PathTool implements Tool {
  readonly cursor = 'crosshair';
  private points: number[] = [];
  private draft: Annotation | null = null;

  constructor(readonly name: 'polygon' | 'polyline' | 'points') {}

  private get shapeType(): ShapeType {
    return this.name;
  }

  onPointerDown(input: PointerInput, context: ToolContext): ToolResult {
    const labelId = context.activeLabelId();
    if (!labelId) return {};

    let position = input.image;
    let snap: Point | null = null;
    if (context.snapEnabled()) {
      const tolerance = screenTolerance(context.viewport(), 10);
      const nearby = context.scene.pickInBox({
        minX: position.x - tolerance,
        minY: position.y - tolerance,
        maxX: position.x + tolerance,
        maxY: position.y + tolerance,
      });
      const snapped = snapToVertex(position, nearby, tolerance);
      if (snapped !== position) {
        position = snapped;
        snap = snapped;
      }
    }

    this.points.push(position.x, position.y);
    this.draft = draftAnnotation(labelId, this.shapeType, [...this.points]);

    // A points annotation is complete the moment the first point lands.
    if (this.name === 'points' && this.points.length >= 2) {
      const created = this.draft;
      this.points = [];
      this.draft = null;
      return { created, draft: null, snap: null };
    }
    return { draft: this.draft, snap };
  }

  onPointerMove(input: PointerInput, _context: ToolContext): ToolResult {
    if (this.points.length === 0 || !this.draft) return {};
    // Show the segment that would be committed by the next click.
    this.draft = {
      ...this.draft,
      points: [...this.points, input.image.x, input.image.y],
    };
    return { draft: this.draft };
  }

  onPointerUp(): ToolResult {
    return {};
  }

  onKey(key: string, _context: ToolContext): ToolResult | null {
    if (key === 'Enter') return this.finish();
    if (key === 'Escape') return this.cancel();
    if (key === 'Backspace' && this.points.length >= 2) {
      this.points.splice(-2, 2);
      if (this.points.length === 0) return this.cancel();
      this.draft = this.draft ? { ...this.draft, points: [...this.points] } : null;
      return { draft: this.draft };
    }
    return null;
  }

  /** Commit the shape if it has enough vertices; otherwise discard it. */
  finish(): ToolResult {
    const required = MIN_VERTICES[this.shapeType];
    if (!this.draft || this.points.length / 2 < required) return this.cancel();
    const created: Annotation = { ...this.draft, points: [...this.points] };
    this.points = [];
    this.draft = null;
    return { created, draft: null, snap: null };
  }

  cancel(): ToolResult {
    this.points = [];
    this.draft = null;
    return { draft: null, snap: null };
  }

  get vertexCount(): number {
    return this.points.length / 2;
  }
}

// ------------------------------------------------------------------------------ pan

export class PanTool implements Tool {
  readonly name = 'pan' as const;
  readonly cursor = 'grab';
  onPointerDown(): ToolResult {
    return {};
  }
  onPointerMove(): ToolResult {
    return {};
  }
  onPointerUp(): ToolResult {
    return {};
  }
  cancel(): ToolResult {
    return {};
  }
}

export function createTool(name: ToolName): Tool {
  switch (name) {
    case 'rectangle':
      return new RectangleTool();
    case 'ellipse':
      return new EllipseTool();
    case 'polygon':
    case 'polyline':
    case 'points':
      return new PathTool(name);
    case 'pan':
      return new PanTool();
    default:
      return new SelectTool();
  }
}

/** Default keyboard map. Single keys, because tool switching happens constantly. */
export const TOOL_SHORTCUTS: Record<string, ToolName> = {
  v: 'select',
  h: 'pan',
  r: 'rectangle',
  p: 'polygon',
  l: 'polyline',
  n: 'points',
  e: 'ellipse',
};

export { boundsOf };
