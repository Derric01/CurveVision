/**
 * Core types for the CurveVision annotation engine.
 *
 * The engine is plain TypeScript: it imports nothing from React, and React talks to it
 * through a small command API. That boundary is what keeps the most performance-sensitive
 * surface in the product testable in Node and stops it degenerating into an
 * unmaintainable component.
 */

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
}

export interface LabelStyle {
  id: string;
  name: string;
  color: string;
  visible: boolean;
  locked: boolean;
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
  | 'scissors';

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
