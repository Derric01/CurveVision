/**
 * Core types for the CurveVision annotation engine.
 *
 * The engine is plain TypeScript: it imports nothing from React, and React talks to it
 * through a small command API. That boundary is what keeps the most performance-sensitive
 * surface in the product testable in Node and stops it degenerating into an
 * unmaintainable component.
 */

import type { SkeletonSchema } from './skeleton';

export type ShapeType =
  | 'rectangle'
  | 'rotated_rectangle'
  | 'polygon'
  | 'polyline'
  | 'points'
  | 'ellipse'
  | 'mask'
  | 'skeleton'
  | 'cuboid';

export type AnnotationSource =
  | 'manual'
  | 'model'
  | 'model_corrected'
  | 'imported'
  | 'interpolated';

/** A point in image pixel space. */
export interface Point {
  x: number;
  y: number;
}

/** An axis-aligned box in image pixel space. */
export interface Box {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
}

/**
 * An instance mask, matching the server's `MaskData` exactly.
 *
 * `width` and `height` are *inclusive* spans of the mask's own sub-rectangle: a 4-wide box
 * at x=10 ends at x=13. See `canvas/mask.ts` for the run-length convention, which is stated
 * there in full because the server states it too and the two must not drift.
 */
export interface MaskData {
  rle: number[];
  left: number;
  top: number;
  width: number;
  height: number;
}

/**
 * One joint of a skeleton, matching the server's `SkeletonElement` exactly.
 *
 * `points` is always a two-number pair, even for a joint nobody could see: that one is
 * written as `(0, 0)` with `outside` set, which is Ultralytics' visibility 0. Omitting it
 * instead would shorten the row and shift every later joint into the wrong slot.
 */
export interface SkeletonElement {
  labelId: string;
  points: number[];
  occluded: boolean;
  outside: boolean;
}

/**
 * One annotation in the scene.
 *
 * `points` is a flat coordinate list, matching the server's representation exactly, so no
 * conversion happens on the hot path between loading and rendering.
 */
export interface Annotation {
  /** Server id, or a client-generated id for an object not yet saved. */
  id: string;
  /** Client id used to reconcile an optimistic object with its server id. */
  clientId?: string;
  labelId: string;
  frame: number;
  shapeType: ShapeType;
  points: number[];
  rotation: number;
  occluded: boolean;
  outside: boolean;
  zOrder: number;
  group?: number | null;
  source: AnnotationSource;
  confidence?: number | null;
  attributes: Record<string, unknown>;
  /** True while the object exists only in the browser. */
  pending?: boolean;
  /** Track this shape was materialised from, when applicable. */
  trackId?: string | null;
  /**
   * Joints, for a skeleton. One entry per joint the label declares, in declared order —
   * including the skipped ones. Absent on every other shape type.
   */
  elements?: SkeletonElement[];
  /** Run-length pixels, for a mask. Absent on every other shape type. */
  mask?: MaskData;
}

export interface LabelStyle {
  id: string;
  name: string;
  color: string;
  visible: boolean;
  locked: boolean;
  /**
   * The skeleton this label is a joint of, when it is one.
   *
   * Joints are in the style list because the canvas has to name and colour them, but they
   * are not labels anybody draws with — the parent is. Anything offering a choice of label
   * filters on this.
   */
  parentId?: string | null;
  /**
   * Joint names and bones, when this label is a skeleton. The tool needs it to know what to
   * ask for next; the renderer needs it to draw the bones. Kept on the style rather than
   * fetched separately so both read one source and cannot disagree about joint order.
   */
  skeleton?: SkeletonSchema;
}

/** Viewport transform: image space -> screen space. */
export interface ViewportState {
  /** Screen pixels per image pixel. */
  scale: number;
  /** Image-space coordinate currently at the canvas origin. */
  offsetX: number;
  offsetY: number;
  width: number;
  height: number;
}

export interface SceneMedia {
  width: number;
  height: number;
  image: CanvasImageSource | null;
}

export type ToolName =
  | 'select'
  | 'pan'
  | 'rectangle'
  | 'polygon'
  | 'polyline'
  | 'points'
  | 'ellipse'
  | 'scissors'
  | 'skeleton';

export interface PointerInput {
  /** Position in image space. */
  image: Point;
  /** Position in screen space. */
  screen: Point;
  shiftKey: boolean;
  ctrlKey: boolean;
  altKey: boolean;
  button: number;
}

/** What the engine reports back to the UI layer. */
export interface EngineEvents {
  selectionChanged: (ids: string[]) => void;
  annotationsChanged: (change: AnnotationChange) => void;
  /** A tool finished creating an object and handed it to the store. */
  created: (annotation: Annotation) => void;
  viewportChanged: (viewport: ViewportState) => void;
  hoverChanged: (id: string | null) => void;
  /** A draft object being drawn right now; null when no draw is in progress. */
  draftChanged: (draft: Annotation | null) => void;
  /** A point picked in image space, when point-picking was armed. Disarms on the click. */
  pointPicked: (point: Point) => void;
  /**
   * What the active tool is waiting for, or `null` when it is waiting for nothing.
   *
   * Exists for interactions a person cannot read off the canvas — placing the joints of a
   * skeleton in a fixed order is the first. Without it the annotator is clicking blind
   * through a seventeen-joint pose.
   */
  toolStatusChanged: (status: string | null) => void;
}

export interface AnnotationChange {
  created: Annotation[];
  updated: Annotation[];
  deletedIds: string[];
}

export const EMPTY_CHANGE: AnnotationChange = Object.freeze({
  created: [],
  updated: [],
  deletedIds: [],
});

/** Minimum vertex counts, mirroring the server's validation so the UI never proposes
 * geometry the API will reject. */
export const MIN_VERTICES: Record<ShapeType, number> = {
  rectangle: 2,
  rotated_rectangle: 2,
  polygon: 3,
  polyline: 2,
  points: 1,
  ellipse: 2,
  mask: 2,
  skeleton: 1,
  cuboid: 8,
};
