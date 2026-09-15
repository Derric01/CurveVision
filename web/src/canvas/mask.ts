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
