/**
 * Instance masks: the run-length encoding, and turning one into pixels to draw.
 *
 * **This is the second statement of a convention that also lives in the server's
 * `formats/rle.py`.** Two implementations of one encoding is exactly the arrangement that
 * drifts, and a drift here is invisible and expensive: the browser would paint one set of
 * pixels and the exporter would write another, with nothing failing. So the rules are
 * repeated here in full rather than referred to, and `mask.test.ts` pins them against the
 * same worked example the Python tests use. If one side changes, that fixture fails.
 *
 * The rules:
 *
 * - A mask is `{ rle, left, top, width, height }`. The box is the mask's own sub-rectangle
 *   in frame coordinates; the runs describe the pixels inside it and nothing outside it.
 * - Runs alternate, **starting with background**. `[0, 4]` is "no background, then four
 *   foreground". A leading zero is normal and load-bearing, not a quirk to strip.
 * - They are read **row-major inside the box** — left to right, then top to bottom.
 * - They sum to `width * height`. A list that stops early leaves the rest background, which
 *   is what trimming a trailing background run produces; `decodeRle` accepts that and
 *   `encodeRle` emits it, so the pair round-trips exactly.
 * - `width` and `height` are *inclusive* spans: a 4-wide box at x=10 ends at x=13. That is
 *   what CVAT's element means and what the server stores.
 */

import type { MaskData } from './types';

/** A mask decoded to a flat row-major `width * height` array, one flag per pixel. */
export interface DecodedMask {
  left: number;
  top: number;
  width: number;
  height: number;
  flags: Uint8Array;
}

/**
 * Runs to a flat row-major array of 0/1, one per pixel of the box.
 *
 * Liberal in what it accepts, because a trailing background run is routinely trimmed: a
 * short list leaves the rest background, and an overrun is truncated at the end of the box.
 * A *negative* run is corrupt rather than a trimming convention, and returns null — silently
 * reading it as zero would shift every pixel after it and paint a mask that is wrong rather
 * than one that is missing.
 */
export function decodeRle(rle: readonly number[], width: number, height: number): Uint8Array | null {
  if (!Number.isInteger(width) || !Number.isInteger(height) || width <= 0 || height <= 0) {
    return null;
  }
  const total = width * height;
  const flags = new Uint8Array(total);
  let index = 0;
  let value = 0; // runs start with background
  for (const run of rle) {
    if (!Number.isFinite(run) || run < 0) return null;
    if (index >= total) break;
    const stop = Math.min(index + run, total);
    if (value === 1) flags.fill(1, index, stop);
    index = stop;
    value = value === 1 ? 0 : 1;
  }
  return flags;
}

/**
 * A flat array of 0/1 back to runs, starting with background.
 *
 * The inverse of `decodeRle`, and the encoder a brush will need. A trailing background run
 * is trimmed, which is why `decodeRle` tolerates a short list — the two halves of that
 * decision live next to each other on purpose.
 */
export function encodeRle(flags: ArrayLike<number>): number[] {
  const runs: number[] = [];
  let value = 0;
  let run = 0;
  for (let i = 0; i < flags.length; i += 1) {
    const flag = flags[i] ? 1 : 0;
    if (flag === value) {
      run += 1;
      continue;
    }
    runs.push(run);
    value = flag;
    run = 1;
  }
  // Ends on foreground, so the final run carries pixels and has to be written. A trailing
  // background run says nothing a reader cannot infer from the box.
  if (value === 1) runs.push(run);
  return runs;
}

/** A stored mask decoded, or `null` when it is missing, malformed or covers nothing. */
export function decodeMask(mask: MaskData | undefined | null): DecodedMask | null {
  if (!mask || !Array.isArray(mask.rle)) return null;
  const { left, top, width, height } = mask;
  if (![left, top, width, height].every((value) => Number.isFinite(value))) return null;
  const flags = decodeRle(mask.rle, Math.trunc(width), Math.trunc(height));
  if (!flags) return null;
  return {
    left: Math.trunc(left),
    top: Math.trunc(top),
    width: Math.trunc(width),
    height: Math.trunc(height),
    flags,
  };
}

/**
 * `[minX, minY, maxX, maxY]` in frame coordinates, both ends inclusive.
 *
 * Returns null rather than a zero box for an unreadable mask, so a caller can fall back to
 * the shape's `points` instead of drawing a dot at the origin.
 */
export function maskBounds(mask: MaskData | undefined | null): [number, number, number, number] | null {
  const decoded = decodeMask(mask);
  if (!decoded) return null;
  return [
    decoded.left,
    decoded.top,
    decoded.left + decoded.width - 1,
    decoded.top + decoded.height - 1,
  ];
}

/** How many pixels a mask actually covers. Zero means it would draw nothing. */
export function maskArea(mask: MaskData | undefined | null): number {
  const decoded = decodeMask(mask);
  if (!decoded) return 0;
  let count = 0;
  for (let i = 0; i < decoded.flags.length; i += 1) {
    if (decoded.flags[i]) count += 1;
  }
  return count;
}

/** Whether a pixel of the frame is inside the mask. Used for hit-testing a click. */
export function maskContains(mask: MaskData | undefined | null, x: number, y: number): boolean {
  const decoded = decodeMask(mask);
  if (!decoded) return false;
  const column = Math.floor(x) - decoded.left;
  const row = Math.floor(y) - decoded.top;
  if (column < 0 || row < 0 || column >= decoded.width || row >= decoded.height) return false;
  return decoded.flags[row * decoded.width + column] === 1;
}

/**
 * RGBA bytes for a decoded mask, one pixel per flag, ready for `putImageData`.
 *
 * Premultiplied nothing and no anti-aliasing: a mask is a per-pixel yes or no, and softening
 * its edge in the editor would show the annotator a boundary that is not the one being
 * stored. `drawImage` scales it to the viewport afterwards, where the browser's smoothing is
 * a display choice rather than a change to the data.
 */
export function maskRgba(
  decoded: DecodedMask,
  rgb: [number, number, number],
  alpha: number,
): Uint8ClampedArray {
  const [r, g, b] = rgb;
  const opacity = Math.round(Math.max(0, Math.min(1, alpha)) * 255);
  const bytes = new Uint8ClampedArray(decoded.flags.length * 4);
  for (let i = 0; i < decoded.flags.length; i += 1) {
    if (!decoded.flags[i]) continue;
    const at = i * 4;
    bytes[at] = r;
    bytes[at + 1] = g;
    bytes[at + 2] = b;
    bytes[at + 3] = opacity;
  }
  return bytes;
}

// ------------------------------------------------------------------------ painting a stroke
//
// The brush works against a buffer sized to the whole *frame*, not to a growing box: a
// stroke can wander in any direction, and re-deriving the box on every pointer-move to keep
// a smaller buffer in step would cost more than the frame-sized `Uint8Array` does. A 4K
// frame is 16 million cells -- a few MB, held only for the length of one stroke, not stored.
// `paintedBounds` finds the box actually worth keeping once the stroke ends, and that
// smaller box is what gets encoded and saved; the frame-sized buffer never leaves the tool.

/**
 * Paint (or erase) a filled disc into a flat `width * height` buffer, clipped to its edges.
 *
 * The `+ 0.5` centres each cell on its pixel rather than its top-left corner, which is what
 * keeps a one-cell brush from reading as an off-centre square instead of a dot.
 */
export function paintDisc(
  flags: Uint8Array,
  width: number,
  height: number,
  cx: number,
  cy: number,
  radius: number,
  value: 0 | 1,
): void {
  const top = Math.max(0, Math.floor(cy - radius));
  const bottom = Math.min(height - 1, Math.ceil(cy + radius));
  const left = Math.max(0, Math.floor(cx - radius));
  const right = Math.min(width - 1, Math.ceil(cx + radius));
  const radiusSquared = radius * radius;
  for (let y = top; y <= bottom; y += 1) {
    const dy = y + 0.5 - cy;
    const row = y * width;
    for (let x = left; x <= right; x += 1) {
      const dx = x + 0.5 - cx;
      if (dx * dx + dy * dy <= radiusSquared) flags[row + x] = value;
    }
  }
}

/**
 * A disc at every point along a segment, so a fast drag between two pointer-move events
 * leaves a continuous stroke rather than a dotted line of discs with gaps between them.
 */
export function paintStroke(
  flags: Uint8Array,
  width: number,
  height: number,
  from: { x: number; y: number },
  to: { x: number; y: number },
  radius: number,
  value: 0 | 1,
): void {
  const distance = Math.hypot(to.x - from.x, to.y - from.y);
  // A step every half-radius keeps consecutive discs overlapping rather than leaving gaps
  // between their edges; at least one step so a from === to call still paints something.
  const steps = Math.max(1, Math.ceil(distance / Math.max(1, radius / 2)));
  for (let i = 0; i <= steps; i += 1) {
    const t = i / steps;
    paintDisc(
      flags,
      width,
      height,
      from.x + (to.x - from.x) * t,
      from.y + (to.y - from.y) * t,
      radius,
      value,
    );
  }
}

/**
 * The smallest box containing every set pixel of a flat `width * height` buffer, or `null`
 * when it holds none. `null` is what tells a brush stroke's caller "there is nothing left to
 * save here" -- an empty box would have to be a special box rather than a plain absence.
 */
export function paintedBounds(
  flags: Uint8Array,
  width: number,
  height: number,
): [number, number, number, number] | null {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (let y = 0; y < height; y += 1) {
    const row = y * width;
    for (let x = 0; x < width; x += 1) {
      if (!flags[row + x]) continue;
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;
    }
  }
  return minX === Infinity ? null : [minX, minY, maxX, maxY];
}

/** A frame-sized buffer's `[minX, minY, maxX, maxY]` (inclusive) sub-rectangle, row-major. */
export function cropFlags(
  flags: Uint8Array,
  width: number,
  box: readonly [number, number, number, number],
): Uint8Array {
  const [minX, minY, maxX, maxY] = box;
  const boxWidth = maxX - minX + 1;
  const boxHeight = maxY - minY + 1;
  const out = new Uint8Array(boxWidth * boxHeight);
  for (let y = 0; y < boxHeight; y += 1) {
    const sourceStart = (minY + y) * width + minX;
    out.set(flags.subarray(sourceStart, sourceStart + boxWidth), y * boxWidth);
  }
  return out;
}

/**
 * Stamp a stored mask's own pixels into a frame-sized buffer, at their absolute position.
 *
 * What lets the brush *edit* an existing mask rather than only draw new ones: seed the
 * working buffer with what is already there, so painting or erasing over it starts from the
 * real shape instead of from blank.
 */
export function paintMaskInto(
  flags: Uint8Array,
  width: number,
  mask: MaskData | undefined | null,
): void {
  const decoded = decodeMask(mask);
  if (!decoded) return;
  for (let y = 0; y < decoded.height; y += 1) {
    const sourceStart = y * decoded.width;
    const destinationStart = (decoded.top + y) * width + decoded.left;
    flags.set(
      decoded.flags.subarray(sourceStart, sourceStart + decoded.width),
      destinationStart,
    );
  }
}

/** `#rrggbb` (or `#rgb`) to the three channels `maskRgba` wants. */
export function rgbOf(color: string): [number, number, number] {
  const hex = color.replace('#', '');
  const full =
    hex.length === 3
      ? hex
          .split('')
          .map((character) => character + character)
          .join('')
      : hex;
  if (full.length !== 6) return [56, 189, 248]; // the same fallback the stroke colour uses
  const value = Number.parseInt(full, 16);
  if (!Number.isFinite(value)) return [56, 189, 248];
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255];
}
