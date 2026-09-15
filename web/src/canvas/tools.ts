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
import { computeFeatures, LiveWire, simplify, type EdgeFeatures } from './scissors';
import {
  buildElements,
  canCommit,
  describeProgress,
  isComplete,
  skeletonPoints,
  type PlacedJoint,
  type SkeletonSchema,
} from './skeleton';

export interface ToolContext {
  scene: Scene;
  viewport: () => ViewportState;
  imageSize: () => { width: number; height: number };
  /** Label to apply to newly created objects. */
  activeLabelId: () => string | null;
  /** Snapping to nearby vertices, so adjacent polygons share a boundary exactly. */
  snapEnabled: () => boolean;
  /**
   * Pixels of the current frame, for tools that read the image rather than only the pointer.
   *
   * Optional and lazy: reading pixels back means rasterising the frame to an offscreen
   * canvas, which no other tool needs and none should pay for. Returns `null` when no frame
   * is loaded, or when the browser refuses the read.
   */
  imageData?: () => ImageData | null;
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
  /**
   * One line for the UI about what the tool is waiting for — "left wrist, joint 3 of 17".
   *
   * `null` clears it. Only tools with a multi-step interaction the annotator cannot infer
   * from the canvas set this; for the rest the shape being drawn says everything.
   */
  status?: string | null;
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

// ------------------------------------------------------------------------- scissors

/**
 * Intelligent scissors: click to anchor, and the wire to your cursor hugs the edge.
 *
 * The bindings deliberately match {@link PathTool} — click to commit, Enter to finish,
 * Backspace to undo the last anchor, Escape to abandon — because switching tools should not
 * mean relearning your hands. What differs is what lands between the clicks: `PathTool`
 * draws a straight segment, this draws the cheapest boundary the image offers.
 *
 * `services`-style separation applies here too: the algorithm lives in `scissors.ts` and
 * knows nothing about tools, pointers or annotations. This class is the state machine that
 * decides *when* to anchor and what to commit.
 */
export class ScissorsTool implements Tool {
  readonly name = 'scissors' as const;
  readonly cursor = 'crosshair';

  /** Committed boundary, flattened as x,y pairs. */
  private committed: number[] = [];
  /** Where each anchor's segment starts in `committed`, so Backspace can unwind exactly. */
  private offsets: number[] = [];
  private wire: LiveWire | null = null;
  private features: EdgeFeatures | null = null;
  /** The image the features were built from, so a frame change rebuilds them. */
  private featureSource: ImageData | null = null;
  private draft: Annotation | null = null;
  private labelId: string | null = null;

  /**
   * How far a vertex may move when the dense pixel path is thinned, in image pixels.
   *
   * The wire arrives one vertex per pixel; a 400-pixel boundary is 400 vertices, which is
   * unusable as a polygon and enormous in an export. One pixel is below what an annotator
   * can see and cuts the count by an order of magnitude.
   */
  private static readonly SIMPLIFY_TOLERANCE = 1;

  private ensureFeatures(context: ToolContext): boolean {
    const image = context.imageData?.() ?? null;
    if (!image) return false;
    if (this.features && this.featureSource === image) return true;
    this.features = computeFeatures(image);
    this.featureSource = image;
    this.wire = new LiveWire(this.features);
    return true;
  }

  onPointerDown(input: PointerInput, context: ToolContext): ToolResult {
    const labelId = context.activeLabelId();
    if (!labelId) return {};
    if (!this.ensureFeatures(context) || !this.wire) {
      // No pixels to read: refuse rather than silently degrading to straight lines, which
      // would look like the tool working badly instead of not running.
      return {};
    }
    this.labelId = labelId;

    if (this.committed.length === 0) {
      this.offsets = [0];
      this.committed = [input.image.x, input.image.y];
    } else {
      // Freeze the wire currently on screen, then re-anchor at its far end.
      const segment = this.segmentTo(input.image);
      this.offsets.push(this.committed.length);
      this.committed.push(...segment);
    }

    this.wire.setAnchor(input.image);
    this.draft = draftAnnotation(labelId, 'polygon', [...this.committed]);
    return { draft: this.draft };
  }

  /** The live wire from the current anchor to `target`, thinned, without the anchor itself. */
  private segmentTo(target: Point): number[] {
    if (!this.wire || !this.wire.hasAnchor) return [target.x, target.y];
    const path = simplify(this.wire.pathTo(target), ScissorsTool.SIMPLIFY_TOLERANCE);
    const flat: number[] = [];
    // Skip index 0: it is the anchor, which the previous segment already committed.
    for (let i = 1; i < path.length; i++) flat.push(path[i]!.x, path[i]!.y);
    return flat.length > 0 ? flat : [target.x, target.y];
  }

  onPointerMove(input: PointerInput, _context: ToolContext): ToolResult {
    if (this.committed.length === 0 || !this.labelId) return {};
    const preview = [...this.committed, ...this.segmentTo(input.image)];
    this.draft = draftAnnotation(this.labelId, 'polygon', preview);
    return { draft: this.draft };
  }

  onPointerUp(): ToolResult {
    return {};
  }

  onKey(key: string, _context: ToolContext): ToolResult | null {
    if (key === 'Enter') return this.finish();
    if (key === 'Escape') return this.cancel();
    if (key === 'Backspace' && this.offsets.length > 0) {
      // Unwind one anchor: drop its segment and re-anchor at the previous one.
      const start = this.offsets.pop()!;
      this.committed = this.committed.slice(0, start === 0 ? 0 : start);
      if (this.committed.length < 2 || !this.wire) return this.cancel();
      const x = this.committed[this.committed.length - 2]!;
      const y = this.committed[this.committed.length - 1]!;
      this.wire.setAnchor({ x, y });
      this.draft = this.labelId
        ? draftAnnotation(this.labelId, 'polygon', [...this.committed])
        : null;
      return { draft: this.draft };
    }
    return null;
  }

  finish(): ToolResult {
    if (!this.draft || this.committed.length / 2 < MIN_VERTICES.polygon) return this.cancel();
    const created: Annotation = { ...this.draft, points: [...this.committed] };
    this.reset();
    return { created, draft: null, snap: null };
  }

  cancel(): ToolResult {
    this.reset();
    return { draft: null, snap: null };
  }

  private reset(): void {
    this.committed = [];
    this.offsets = [];
    this.draft = null;
    this.labelId = null;
    // Features are kept: they belong to the frame, not to the shape being drawn, and
    // recomputing them on every Escape would make the tool feel broken.
  }

  get vertexCount(): number {
    return this.committed.length / 2;
  }
}

// ------------------------------------------------------------------------- skeleton

/**
 * Place a label's declared joints, one at a time, in the order the label declares them.
 *
 * Skeletons already round-tripped through the model, the API and `yolo_pose`; nothing in
 * the application could draw one, so the platform exported a dataset shape it could not
 * produce. This is that missing half.
 *
 * **Order is the whole design.** `yolo_pose` writes `px py v` positionally, so the third
 * triple *is* the third declared joint — there is no name in the file to correct a
 * mis-ordering. The tool therefore walks the joints strictly in order and never lets the
 * annotator choose which one to place next, and a joint nobody can see is **skipped, not
 * omitted**: it becomes a zero-visibility element in its own slot. Omitting it would
 * shorten the row and move every later joint one place left, which is a dataset that looks
 * correct and teaches a model to put elbows where wrists are.
 *
 * Two endings, because the common case should not need a keystroke: placing the last joint
 * finishes the skeleton by itself, and `Enter` finishes early with the rest skipped.
 */
export class SkeletonTool implements Tool {
  readonly name = 'skeleton' as const;
  readonly cursor = 'crosshair';

  private placement: (PlacedJoint | null)[] = [];
  private labelId: string | null = null;

  onPointerDown(input: PointerInput, context: ToolContext): ToolResult {
    const labelId = context.activeLabelId();
    if (!labelId) return {};
    const schema = context.scene.skeletonFor(labelId);
    // The active label is not a skeleton. Doing nothing is right; the panel says why,
    // because a canvas that silently ignores clicks is indistinguishable from a broken one.
    if (!schema) return {};

    // Switching label mid-skeleton would mix one label's joints into another's slots.
    if (this.labelId !== null && this.labelId !== labelId) this.reset();
    this.labelId = labelId;

    const size = context.imageSize();
    const [x = 0, y = 0] = clampToImage([input.image.x, input.image.y], size.width, size.height);
    // Alt marks a joint the annotator can locate but cannot see clearly -- Ultralytics'
    // visibility 1, as opposed to 2 for plainly visible.
    this.placement = [...this.placement, { point: { x, y }, occluded: input.altKey }];

    if (isComplete(schema, this.placement)) return this.finish(context);
    return { draft: this.draft(context), status: describeProgress(schema, this.placement) };
  }

  onPointerMove(_input: PointerInput, _context: ToolContext): ToolResult {
    // Nothing follows the pointer: a joint is where it is clicked, and a line trailing to
    // the cursor would suggest the next joint connects to the last one, which the bones say
    // it may well not.
    return {};
  }

  onPointerUp(): ToolResult {
    return {};
  }

  onKey(key: string, context: ToolContext): ToolResult | null {
    const schema = this.schema(context);
    if (!schema) return null;

    if (key === 'Enter') {
      if (this.placement.length === 0) return null;
      return this.finish(context);
    }
    // Skip the joint being asked for. Recorded as a decision, not a gap -- see the class
    // comment for why that distinction is the point of the whole tool.
    if (key === 'x' || key === 'X') {
      if (isComplete(schema, this.placement)) return null;
      this.placement = [...this.placement, null];
      if (isComplete(schema, this.placement)) {
        // Every joint skipped is not a skeleton, it is a claim with no content.
        if (!canCommit(this.placement)) return this.cancel();
        return this.finish(context);
      }
      return { draft: this.draft(context), status: describeProgress(schema, this.placement) };
    }
    if (key === 'Backspace') {
      if (this.placement.length === 0) return null;
      this.placement = this.placement.slice(0, -1);
      return { draft: this.draft(context), status: describeProgress(schema, this.placement) };
    }
    if (key === 'Escape') {
      if (this.placement.length === 0) return null;
      return this.cancel();
    }
    return null;
  }

  cancel(): ToolResult {
    this.reset();
    return { draft: null, status: null };
  }

  /** What the panel shows: which joint is being asked for, and how far through. */
  progress(context: ToolContext): string | null {
    const schema = this.schema(context);
    if (!schema) return null;
    return describeProgress(schema, this.placement);
  }

  private schema(context: ToolContext): SkeletonSchema | null {
    const labelId = this.labelId ?? context.activeLabelId();
    if (!labelId) return null;
    return context.scene.skeletonFor(labelId) ?? null;
  }

  private finish(context: ToolContext): ToolResult {
    const schema = this.schema(context);
    if (!schema || !canCommit(this.placement)) return this.cancel();

    const elements = buildElements(schema, this.placement);
    const created: Annotation = {
      ...draftAnnotation(schema.labelId, 'skeleton', skeletonPoints(elements)),
      elements,
    };
    this.reset();
    return { created, draft: null, status: null };
  }

  /**
   * The skeleton as it stands, for the overlay.
   *
   * Built from `buildElements` rather than from the placement directly, so what is on
   * screen mid-draw is exactly what would be committed -- a preview drawn a second way is
   * a second implementation, and the two drift.
   */
  private draft(context: ToolContext): Annotation | null {
    const schema = this.schema(context);
    if (!schema || this.placement.length === 0) return null;
    const elements = buildElements(schema, this.placement);
    return {
      ...draftAnnotation(schema.labelId, 'skeleton', skeletonPoints(elements)),
      elements,
    };
  }

  private reset(): void {
    this.placement = [];
    this.labelId = null;
  }

  /** Joints decided so far, for tests and for the panel. */
  get decided(): number {
    return this.placement.length;
  }
}

export function createTool(name: ToolName): Tool {
  switch (name) {
    case 'scissors':
      return new ScissorsTool();
    case 'skeleton':
      return new SkeletonTool();
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
  s: 'scissors',
  // `k` is the editor's add-keyframe and `n` is the point tool, so joints get `j`.
  j: 'skeleton',
};

export { boundsOf };
