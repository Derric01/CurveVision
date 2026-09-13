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
import type { Scene } from './scene';
import { imageToScreen, visibleBox } from './viewport';
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

export class Renderer {
  private lastDrawnShapes = 0;

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
    // machine proposed and what a human placed.
    if (annotation.source === 'model' || draft) context.setLineDash([6, 4]);
    if (annotation.occluded) context.setLineDash([2, 3]);

    this.tracePath(context, viewport, annotation);

    if (isFillable(annotation)) context.fill();
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
