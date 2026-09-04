/**
 * Viewport: the transform between image space and screen space.
 *
 * Kept separate from the renderer because pan/zoom is the interaction annotators perform
 * most, and it needs to be exactly right: zooming must hold the point under the cursor
 * still, or the tool feels broken within seconds of use.
 */

import type { Box, Point, ViewportState } from './types';

export const MIN_SCALE = 0.02;
export const MAX_SCALE = 64;

export function createViewport(width = 0, height = 0): ViewportState {
  return { scale: 1, offsetX: 0, offsetY: 0, width, height };
}

export function imageToScreen(viewport: ViewportState, point: Point): Point {
  return {
    x: (point.x - viewport.offsetX) * viewport.scale,
    y: (point.y - viewport.offsetY) * viewport.scale,
  };
}

export function screenToImage(viewport: ViewportState, point: Point): Point {
  return {
    x: point.x / viewport.scale + viewport.offsetX,
    y: point.y / viewport.scale + viewport.offsetY,
  };
}

/** The image-space rectangle currently visible. The renderer culls against this. */
export function visibleBox(viewport: ViewportState): Box {
  return {
    minX: viewport.offsetX,
    minY: viewport.offsetY,
    maxX: viewport.offsetX + viewport.width / viewport.scale,
    maxY: viewport.offsetY + viewport.height / viewport.scale,
  };
}

export function clampScale(scale: number): number {
  return Math.max(MIN_SCALE, Math.min(MAX_SCALE, scale));
}

/**
 * Zoom about a fixed screen point.
 *
 * The image coordinate under the cursor is computed before and after the scale change and
 * the offset is corrected by the difference, which is what makes the pixel under the
 * pointer stay put.
 */
export function zoomAt(
  viewport: ViewportState,
  screenPoint: Point,
  factor: number,
): ViewportState {
  const scale = clampScale(viewport.scale * factor);
  if (scale === viewport.scale) return viewport;

  const before = screenToImage(viewport, screenPoint);
  const after = screenToImage({ ...viewport, scale }, screenPoint);
  return {
    ...viewport,
    scale,
    offsetX: viewport.offsetX + (before.x - after.x),
    offsetY: viewport.offsetY + (before.y - after.y),
  };
}

export function panBy(viewport: ViewportState, screenDx: number, screenDy: number): ViewportState {
  return {
    ...viewport,
    offsetX: viewport.offsetX - screenDx / viewport.scale,
    offsetY: viewport.offsetY - screenDy / viewport.scale,
  };
}

/** Fit an image of `imageWidth` x `imageHeight` into the viewport, with a small margin. */
export function fitToImage(
  viewport: ViewportState,
  imageWidth: number,
  imageHeight: number,
  margin = 0.04,
): ViewportState {
  if (imageWidth <= 0 || imageHeight <= 0 || viewport.width <= 0 || viewport.height <= 0) {
    return viewport;
  }
  const scale = clampScale(
    Math.min(viewport.width / imageWidth, viewport.height / imageHeight) * (1 - margin),
  );
  return {
    ...viewport,
    scale,
    offsetX: (imageWidth - viewport.width / scale) / 2,
    offsetY: (imageHeight - viewport.height / scale) / 2,
  };
}

/** Centre the viewport on `box`, zooming so it occupies most of the canvas. */
export function focusOn(viewport: ViewportState, box: Box, padding = 2.5): ViewportState {
  const width = Math.max(1, box.maxX - box.minX);
  const height = Math.max(1, box.maxY - box.minY);
  const scale = clampScale(
    Math.min(viewport.width / (width * padding), viewport.height / (height * padding)),
  );
  const centreX = (box.minX + box.maxX) / 2;
  const centreY = (box.minY + box.maxY) / 2;
  return {
    ...viewport,
    scale,
    offsetX: centreX - viewport.width / (2 * scale),
    offsetY: centreY - viewport.height / (2 * scale),
  };
}

/** Convert a screen-pixel tolerance into image space, so picking feels the same at any zoom. */
export function screenTolerance(viewport: ViewportState, pixels = 6): number {
  return pixels / viewport.scale;
}
