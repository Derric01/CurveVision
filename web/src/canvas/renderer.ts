/**
 * Canvas2D renderer.
 *
 * Three layers, painted independently:
 *
 *   media    the frame image; repaints only on frame or viewport change
 *   shapes   the annotations; repaints on data or viewport change
 *   overlay  hover, selection handles, the in-progress draft, the cursor crosshair;
 *            repaints on every pointer move
 *
 * Separating them is what keeps a pointer-move cheap: hovering repaints a transparent
 * overlay, not 100k shapes. Combined with viewport culling in `Scene.visible`, frame time
 * tracks what is on screen rather than what is in the dataset.
 */

import { annotationBounds } from './geometry';
import { decodeMask, maskRgba, rgbOf, type DecodedMask } from './mask';
import { bones, drawableJoints } from './skeleton';
import type { Scene } from './scene';
import { imageToScreen, visibleBox } from './viewport';
import { isUnreviewed } from './types';
import type { Annotation, SceneMedia, ViewportState } from './types';

export interface RendererLayers {
  media: CanvasRenderingContext2D;
  shapes: CanvasRenderingContext2D;
  overlay: CanvasRenderingContext2D;
}

/** A review issue pinned to a point on this frame. */
export interface OverlayPin {
  id: string;
  x: number;
  y: number;
  resolved: boolean;
  /** The issue whose thread is open in the sidebar. */
  active: boolean;
}

export interface OverlayState {
  /** Object being drawn right now. */
  draft: Annotation | null;
  /** Rubber-band selection rectangle, in image space. */
  marquee: { minX: number; minY: number; maxX: number; maxY: number } | null;
  cursor: { x: number; y: number } | null;
  /** Vertex snap indicator. */
  snap: { x: number; y: number } | null;
  /**
   * Issue pins for the frame on screen.
   *
   * Overlay rather than a shape layer: a pin is not an annotation, it does not export, and
   * it must not be pickable by the selection tools — a reviewer's marker is not something
   * you drag, delete with the rest of a marquee, or accidentally include in a dataset.
   */
  pins: OverlayPin[];
}

export const EMPTY_OVERLAY: OverlayState = {
  draft: null,
  marquee: null,
  cursor: null,
  snap: null,
  pins: [],
};

const HANDLE_SIZE = 7;
/** Screen pixels, not image pixels — a pin marks a place and must stay findable. */
const PIN_RADIUS = 9;
const SELECTED_LINE_WIDTH = 2.5;
const DEFAULT_LINE_WIDTH = 1.75;
/**
 * How solid a mask's own pixels are.
 *
 * Higher than the translucent fill a polygon gets, because a mask's *shape* is the
 * information -- a ragged boundary you can barely see is the same as not drawing it -- and
 * low enough that the frame underneath is still readable, which is what an annotator is
 * checking the mask against.
 */
const MASK_FILL_ALPHA = 0.55;

export class Renderer {
  private lastDrawnShapes = 0;
  /**
   * Decoded mask bitmaps, by shape id.
   *
   * A plain Map rather than a WeakMap: the key is a string id, not the object. It is
   * pruned when a shape's mask is replaced or becomes unreadable, and `forgetMasks` clears
   * it wholesale when the frame changes, so it tracks what is on screen rather than
   * growing with every shape ever rendered.
   */
  private maskCache = new Map<
    string,
    { payload: unknown; color: string; bitmap: DecodedMask & { canvas: HTMLCanvasElement } }
  >();

  constructor(
    private readonly layers: RendererLayers,
    private readonly scene: Scene,
  ) {}

  /** How many shapes the last shape-layer pass painted. Used by the perf HUD and benches. */
  get lastShapeCount(): number {
    return this.lastDrawnShapes;
  }

  resize(width: number, height: number, dpr: number): void {
    for (const context of [this.layers.media, this.layers.shapes, this.layers.overlay]) {
      const canvas = context.canvas;
      canvas.width = Math.max(1, Math.floor(width * dpr));
      canvas.height = Math.max(1, Math.floor(height * dpr));
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
  }

  /**
   * Drop every cached mask bitmap. Called when the frame changes.
   *
   * Without it the cache keeps one canvas per mask for every frame ever visited, which on
   * a long video is a real leak; and a shape that was pending when it was cached keeps an
   * entry under its client id after the server assigns a real one.
   */
  forgetMasks(): void {
    this.maskCache.clear();
  }

  drawMedia(viewport: ViewportState, media: SceneMedia): void {
    const context = this.layers.media;
    context.clearRect(0, 0, viewport.width, viewport.height);
    if (!media.image) return;
    // Belt and braces: an image that failed to load draws as a throw rather than as
    // nothing, and one bad frame should never take down the whole canvas.
    if (media.image instanceof HTMLImageElement && !media.image.complete) return;
    if (media.image instanceof HTMLImageElement && media.image.naturalWidth === 0) return;

    const origin = imageToScreen(viewport, { x: 0, y: 0 });
    // Nearest-neighbour above 2x: annotators zoom in to place a boundary on an exact
    // pixel, and smoothing actively hides the pixel they are aiming at.
    context.imageSmoothingEnabled = viewport.scale < 2;
    context.drawImage(
      media.image,
      origin.x,
      origin.y,
      media.width * viewport.scale,
      media.height * viewport.scale,
    );
  }

  drawShapes(viewport: ViewportState): void {
    const context = this.layers.shapes;
    context.clearRect(0, 0, viewport.width, viewport.height);

    const annotations = this.scene.visible(visibleBox(viewport));
    this.lastDrawnShapes = annotations.length;

    for (const annotation of annotations) {
      this.paintAnnotation(context, viewport, annotation, this.scene.isSelected(annotation.id));
    }

    if (this.scene.showLabels) {
      for (const annotation of annotations) {
        this.paintLabel(context, viewport, annotation);
      }
    }
  }

  drawOverlay(viewport: ViewportState, overlay: OverlayState): void {
    const context = this.layers.overlay;
    context.clearRect(0, 0, viewport.width, viewport.height);

    const hoveredId = this.scene.hoveredId;
    if (hoveredId && !this.scene.isSelected(hoveredId)) {
      const hovered = this.scene.get(hoveredId);
      if (hovered) this.paintHover(context, viewport, hovered);
    }

    for (const annotation of this.scene.selected) {
      this.paintHandles(context, viewport, annotation);
    }

    if (overlay.draft) {
      this.paintAnnotation(context, viewport, overlay.draft, false, true);
      this.paintHandles(context, viewport, overlay.draft);
    }

    if (overlay.marquee) {
      const start = imageToScreen(viewport, { x: overlay.marquee.minX, y: overlay.marquee.minY });
      const end = imageToScreen(viewport, { x: overlay.marquee.maxX, y: overlay.marquee.maxY });
      context.save();
      context.strokeStyle = '#2dd4bf';
      context.fillStyle = 'rgba(45, 212, 191, 0.12)';
      context.lineWidth = 1;
      context.setLineDash([4, 3]);
      context.fillRect(start.x, start.y, end.x - start.x, end.y - start.y);
      context.strokeRect(start.x, start.y, end.x - start.x, end.y - start.y);
      context.restore();
    }

    if (overlay.snap) {
      const point = imageToScreen(viewport, overlay.snap);
      context.save();
      context.strokeStyle = '#fbbf24';
      context.lineWidth = 2;
      context.beginPath();
      context.arc(point.x, point.y, 6, 0, Math.PI * 2);
      context.stroke();
      context.restore();
    }

    for (const pin of overlay.pins) this.paintPin(context, viewport, pin);
  }

  /**
   * One issue pin.
   *
   * Drawn at a fixed screen size rather than scaled with the image: a pin marks a place, and
   * a marker that shrinks to nothing at low zoom stops doing the one job it has. The dot at
   * the centre is what says *which* pixel — a ring alone leaves the exact point ambiguous at
   * the moment the reviewer is trying to be precise.
   */
  private paintPin(
    context: CanvasRenderingContext2D,
    viewport: ViewportState,
    pin: OverlayPin,
  ): void {
    const point = imageToScreen(viewport, { x: pin.x, y: pin.y });
    const colour = pin.resolved ? '#64748b' : '#fbbf24';
    context.save();
    context.globalAlpha = pin.resolved ? 0.65 : 1;

    // A dark halo first, so the pin stays legible over a light frame as well as a dark one.
    context.strokeStyle = 'rgba(2, 6, 23, 0.75)';
    context.lineWidth = pin.active ? 5 : 4;
    context.beginPath();
    context.arc(point.x, point.y, PIN_RADIUS, 0, Math.PI * 2);
    context.stroke();

    context.strokeStyle = colour;
    context.lineWidth = pin.active ? 2.5 : 1.5;
    context.beginPath();
    context.arc(point.x, point.y, PIN_RADIUS, 0, Math.PI * 2);
    context.stroke();

    context.fillStyle = colour;
    context.beginPath();
    context.arc(point.x, point.y, pin.active ? 2.5 : 2, 0, Math.PI * 2);
    context.fill();
    context.restore();
  }

  // ------------------------------------------------------------------- painting

  private paintAnnotation(
    context: CanvasRenderingContext2D,
    viewport: ViewportState,
    annotation: Annotation,
    selected: boolean,
    draft = false,
  ): void {
    const color = this.scene.labelColor(annotation.labelId);
    context.save();
    context.strokeStyle = selected ? '#ffffff' : color;
    context.fillStyle = withAlpha(color, this.scene.fillOpacity);
    context.lineWidth = selected ? SELECTED_LINE_WIDTH : DEFAULT_LINE_WIDTH;

    // Model suggestions are drawn dashed so an annotator can tell at a glance what a
    // machine proposed and what a human placed. Only the ones still *awaiting* a decision:
    // accepting one clears its confidence and keeps `source = "model"` for provenance, and
    // if that still drew dashed, accepting would change nothing anybody could see. See
    // `features/editor/suggestions.ts`, where the pair is written down once.
    if (isUnreviewed(annotation) || draft) context.setLineDash([6, 4]);
    if (annotation.occluded) context.setLineDash([2, 3]);

    if (annotation.shapeType === 'skeleton') this.paintBones(context, viewport, annotation);
    const paintedPixels =
      annotation.shapeType === 'mask' && this.paintMask(context, viewport, annotation, color);

    this.tracePath(context, viewport, annotation);

    // A mask that painted its own pixels does not also get a translucent fill over its
    // bounding box: that would tint the empty corners exactly like the covered ones, which
    // is the misreading this whole change exists to remove.
    if (isFillable(annotation) && !paintedPixels) context.fill();
    context.stroke();

    if (selected) {
      // A second, coloured stroke under the white one keeps the label's colour readable
      // while still making selection unmistakable.
      context.setLineDash([]);
      context.strokeStyle = color;
      context.lineWidth = 1;
      context.stroke();
    }
    context.restore();
  }

  /**
   * A mask's own pixels, drawn at the viewport transform. Returns whether anything landed.
   *
   * Until this existed a mask was drawn through the same branch as a rectangle: an empty box
   * where the pixels were. An annotator could not tell a mask covering a whole car from one
   * covering its wing mirror, and could not review an imported one at all.
   *
   * The decoded bitmap is cached per shape, keyed on the mask payload's identity. Decoding
   * run-lengths and building RGBA bytes on every repaint would put a megapixel loop on the
   * pointer-move path -- and the payload is replaced rather than mutated whenever the shape
   * changes, so identity is a sound cache key and needs no version counter.
   */
  private paintMask(
    context: CanvasRenderingContext2D,
    viewport: ViewportState,
    annotation: Annotation,
    color: string,
  ): boolean {
    const bitmap = this.maskBitmap(annotation, color);
    if (!bitmap) return false;

    const { canvas, left, top, width, height } = bitmap;
    const topLeft = imageToScreen(viewport, { x: left, y: top });
    const bottomRight = imageToScreen(viewport, { x: left + width, y: top + height });

    context.save();
    // Nearest-neighbour: a mask is a per-pixel yes or no, and a smoothed edge shows the
    // annotator a boundary that is not the one being stored. At a zoom below 1:1 the browser
    // is downsampling either way, but at the zoom somebody actually edits at this is the
    // difference between seeing the data and seeing an impression of it.
    context.imageSmoothingEnabled = false;
    context.drawImage(
      canvas,
      topLeft.x,
      topLeft.y,
      bottomRight.x - topLeft.x,
      bottomRight.y - topLeft.y,
    );
    context.restore();
    return true;
  }

  private maskBitmap(
    annotation: Annotation,
    color: string,
  ): (DecodedMask & { canvas: HTMLCanvasElement }) | null {
    const payload = annotation.mask;
    if (!payload) return null;

    const cached = this.maskCache.get(annotation.id);
    if (cached && cached.payload === payload && cached.color === color) return cached.bitmap;

    const decoded = decodeMask(payload);
    if (!decoded) {
      // Malformed, or covering nothing. Forget any stale bitmap and let the outline stand
      // in -- drawing the previous mask's pixels would be worse than drawing none.
      this.maskCache.delete(annotation.id);
      return null;
    }

    const canvas = document.createElement('canvas');
    canvas.width = decoded.width;
    canvas.height = decoded.height;
    const target = canvas.getContext('2d');
    if (!target) return null;
    const image = target.createImageData(decoded.width, decoded.height);
    image.data.set(maskRgba(decoded, rgbOf(color), MASK_FILL_ALPHA));
    target.putImageData(image, 0, 0);

    const bitmap = { ...decoded, canvas };
    this.maskCache.set(annotation.id, { payload, color, bitmap });
    return bitmap;
  }

  /**
   * The bones, drawn under the joints.
   *
   * A bone whose endpoint was skipped is not drawn at all: a line to where a hidden joint
   * "would have been" is indistinguishable on screen from one somebody annotated, which is
   * the one thing a keypoint tool must never produce. `bones` drops those; this only draws
   * what it returns.
   */
  private paintBones(
    context: CanvasRenderingContext2D,
    viewport: ViewportState,
    annotation: Annotation,
  ): void {
    const schema = this.scene.skeletonFor(annotation.labelId);
    if (!schema || !annotation.elements) return;
    const segments = bones(schema.edges, annotation.elements);
    if (segments.length === 0) return;

    context.save();
    context.setLineDash([]);
    context.lineWidth = Math.max(1, context.lineWidth - 0.5);
    context.beginPath();
    for (const [start, end] of segments) {
      const from = imageToScreen(viewport, start);
      const to = imageToScreen(viewport, end);
      context.moveTo(from.x, from.y);
      context.lineTo(to.x, to.y);
    }
    context.stroke();
    context.restore();
  }

  private tracePath(
    context: CanvasRenderingContext2D,
    viewport: ViewportState,
    annotation: Annotation,
  ): void {
    const { shapeType, points } = annotation;
    context.beginPath();

    if (shapeType === 'rectangle' || shapeType === 'rotated_rectangle' || shapeType === 'mask') {
      const box = annotationBounds(annotation);
      const topLeft = imageToScreen(viewport, { x: box.minX, y: box.minY });
      const bottomRight = imageToScreen(viewport, { x: box.maxX, y: box.maxY });
      context.rect(
        topLeft.x,
        topLeft.y,
        bottomRight.x - topLeft.x,
        bottomRight.y - topLeft.y,
      );
      return;
    }

    if (shapeType === 'ellipse') {
      const [cx = 0, cy = 0, rx = 0, ry = 0] = points;
      const centre = imageToScreen(viewport, { x: cx, y: cy });
      context.ellipse(
        centre.x,
        centre.y,
        Math.abs(rx) * viewport.scale,
        Math.abs(ry) * viewport.scale,
        ((annotation.rotation ?? 0) * Math.PI) / 180,
        0,
        Math.PI * 2,
      );
      return;
    }

    if (shapeType === 'skeleton' && annotation.elements) {
      // From the elements rather than from `points`, because a skipped joint has no
      // position and must not be drawn at the origin, where it would look like a joint
      // somebody actually placed in the corner of the image.
      for (const joint of drawableJoints(annotation.elements)) {
        const screen = imageToScreen(viewport, joint.point);
        const radius = joint.occluded ? 3 : 4.5;
        context.moveTo(screen.x + radius, screen.y);
        context.arc(screen.x, screen.y, radius, 0, Math.PI * 2);
      }
      return;
    }

    if (shapeType === 'points' || shapeType === 'skeleton') {
      for (let i = 0; i + 1 < points.length; i += 2) {
        const screen = imageToScreen(viewport, { x: points[i]!, y: points[i + 1]! });
        context.moveTo(screen.x + 4, screen.y);
        context.arc(screen.x, screen.y, 4, 0, Math.PI * 2);
      }
      return;
    }

    for (let i = 0; i + 1 < points.length; i += 2) {
      const screen = imageToScreen(viewport, { x: points[i]!, y: points[i + 1]! });
      if (i === 0) context.moveTo(screen.x, screen.y);
      else context.lineTo(screen.x, screen.y);
    }
    if (shapeType === 'polygon') context.closePath();
  }

  private paintLabel(
    context: CanvasRenderingContext2D,
    viewport: ViewportState,
    annotation: Annotation,
  ): void {
    const box = annotationBounds(annotation);
    const anchor = imageToScreen(viewport, { x: box.minX, y: box.minY });
    if (anchor.y < -20 || anchor.y > viewport.height + 20) return;

    let text = this.scene.labelName(annotation.labelId);
    if (annotation.confidence != null) {
      text += ` ${Math.round(annotation.confidence * 100)}%`;
    }

    context.save();
    context.font = '11px ui-sans-serif, system-ui, sans-serif';
    context.textBaseline = 'bottom';
    const width = context.measureText(text).width + 8;
    const color = this.scene.labelColor(annotation.labelId);

    context.fillStyle = color;
    context.fillRect(anchor.x, anchor.y - 15, width, 15);
    context.fillStyle = readableTextColor(color);
    context.fillText(text, anchor.x + 4, anchor.y - 3);
    context.restore();
  }

  private paintHover(
    context: CanvasRenderingContext2D,
    viewport: ViewportState,
    annotation: Annotation,
  ): void {
    context.save();
    context.strokeStyle = '#5eead4';
    context.lineWidth = 3;
    context.globalAlpha = 0.9;
    this.tracePath(context, viewport, annotation);
    context.stroke();
    context.restore();
  }

  private paintHandles(
    context: CanvasRenderingContext2D,
    viewport: ViewportState,
    annotation: Annotation,
  ): void {
    const half = HANDLE_SIZE / 2;
    context.save();
    context.fillStyle = '#ffffff';
    context.strokeStyle = '#0d9488';
    context.lineWidth = 1.5;

    for (const point of handlePoints(annotation)) {
      const screen = imageToScreen(viewport, point);
      context.fillRect(screen.x - half, screen.y - half, HANDLE_SIZE, HANDLE_SIZE);
      context.strokeRect(screen.x - half, screen.y - half, HANDLE_SIZE, HANDLE_SIZE);
    }
    context.restore();
  }
}

/** Where the draggable handles sit for a given shape. */
export function handlePoints(annotation: Annotation): { x: number; y: number }[] {
  const { shapeType, points } = annotation;
  if (shapeType === 'rectangle' || shapeType === 'rotated_rectangle' || shapeType === 'mask') {
    const box = annotationBounds(annotation);
    return [
      { x: box.minX, y: box.minY },
      { x: box.maxX, y: box.minY },
      { x: box.maxX, y: box.maxY },
      { x: box.minX, y: box.maxY },
    ];
  }
  if (shapeType === 'ellipse') {
    const [cx = 0, cy = 0, rx = 0, ry = 0] = points;
    return [
      { x: cx + rx, y: cy },
      { x: cx, y: cy + ry },
    ];
  }
  const vertices: { x: number; y: number }[] = [];
  for (let i = 0; i + 1 < points.length; i += 2) {
    vertices.push({ x: points[i]!, y: points[i + 1]! });
  }
  return vertices;
}

function isFillable(annotation: Annotation): boolean {
  return (
    annotation.shapeType === 'rectangle' ||
    annotation.shapeType === 'rotated_rectangle' ||
    annotation.shapeType === 'polygon' ||
    annotation.shapeType === 'ellipse' ||
    annotation.shapeType === 'mask'
  );
}

/** `#rrggbb` plus an alpha channel, without pulling in a colour library. */
export function withAlpha(color: string, alpha: number): string {
  const hex = color.replace('#', '');
  if (hex.length !== 6 && hex.length !== 3) return color;
  const full =
    hex.length === 3
      ? hex
          .split('')
          .map((c) => c + c)
          .join('')
      : hex;
  const r = parseInt(full.slice(0, 2), 16);
  const g = parseInt(full.slice(2, 4), 16);
  const b = parseInt(full.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/** Black or white, whichever stays legible on `background`. */
export function readableTextColor(background: string): string {
  const hex = background.replace('#', '');
  if (hex.length < 6) return '#000000';
  const r = parseInt(hex.slice(0, 2), 16);
  const g = parseInt(hex.slice(2, 4), 16);
  const b = parseInt(hex.slice(4, 6), 16);
  // Rec. 709 luma: matches how the eye weights the channels.
  const luma = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
  return luma > 0.6 ? '#0d1117' : '#ffffff';
}
