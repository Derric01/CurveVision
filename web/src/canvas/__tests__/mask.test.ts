/**
 * The mask encoding, and what the browser does with one.
 *
 * The first block is the important one. `canvas/mask.ts` and the server's `formats/rle.py`
 * are two implementations of one encoding, and a drift between them is invisible: the
 * browser paints one set of pixels, the exporter writes another, and nothing fails. So the
 * worked example here is **the same one the Python tests use** — an L in a 4x3 box, encoding
 * to `[0, 3, 1, 1]`, covering four named absolute pixels. If either side changes its mind
 * about the convention, that fixture goes red on that side.
 */

import { describe, expect, it } from 'vitest';
import type { Annotation, MaskData } from '../types';
import {
  cropFlags,
  decodeMask,
  decodeRle,
  encodeRle,
  maskArea,
  maskBounds,
  maskContains,
  maskRgba,
  paintDisc,
  paintMaskInto,
  paintStroke,
  paintedBounds,
  rgbOf,
} from '../mask';
import { annotationBounds, hitTest } from '../geometry';

/** The shared fixture:  ###.   at (10, 10) in a 4x3 box.
 *                       #...
 *                       ....                                                          */
const L_FLAGS = [1, 1, 1, 0, 1, 0, 0, 0, 0, 0, 0, 0];
const L_RLE = [0, 3, 1, 1];
const L_PIXELS: [number, number][] = [
  [10, 10],
  [11, 10],
  [12, 10],
  [10, 11],
];

function lMask(left = 10, top = 10): MaskData {
  return { rle: [...L_RLE], left, top, width: 4, height: 3 };
}

function maskAnnotation(mask: MaskData | undefined, points: number[] = []): Annotation {
  return {
    id: 'm1',
    labelId: 'car',
    frame: 0,
    shapeType: 'mask',
    points,
    rotation: 0,
    occluded: false,
    outside: false,
    zOrder: 0,
    source: 'manual',
    attributes: {},
    ...(mask ? { mask } : {}),
  };
}

describe('the encoding, pinned against the server', () => {
  // If this ever needs changing, `server/curvevision/formats/rle.py` needs the same change
  // in the same commit, and its own test carries the identical numbers.
  it('encodes the shared worked example to the shared run list', () => {
    expect(encodeRle(L_FLAGS)).toEqual(L_RLE);
  });

  it('decodes it back to the same pixels', () => {
    expect(Array.from(decodeRle(L_RLE, 4, 3) ?? [])).toEqual(L_FLAGS);
  });

  it('places it on the frame at the pixels the server names', () => {
    const covered = L_PIXELS.every(([x, y]) => maskContains(lMask(), x, y));
    expect(covered).toBe(true);
    expect(maskArea(lMask())).toBe(L_PIXELS.length);
  });

  it('treats a leading zero as meaningful rather than stripping it', () => {
    expect(encodeRle([1, 1, 0])).toEqual([0, 2]);
    expect(Array.from(decodeRle([0, 2], 3, 1) ?? [])).toEqual([1, 1, 0]);
  });

  it('reads the box spans as inclusive, the way CVAT writes them', () => {
    expect(maskBounds(lMask())).toEqual([10, 10, 13, 12]);
  });
});

describe('decodeRle', () => {
  it('round-trips every shape of a 4x3 box', () => {
    for (const flags of [
      new Array(12).fill(0),
      new Array(12).fill(1),
      [1, ...new Array(11).fill(0)],
      [...new Array(11).fill(0), 1],
      L_FLAGS,
    ]) {
      expect(Array.from(decodeRle(encodeRle(flags), 4, 3) ?? [])).toEqual(flags);
    }
  });

  it('encodes an empty mask to nothing rather than a run of zeros', () => {
    expect(encodeRle(new Array(12).fill(0))).toEqual([]);
  });

  // Trimming a trailing background run is normal, so refusing a short list would make this
  // module unable to read its own output.
  it('leaves the rest background when the runs stop early', () => {
    expect(Array.from(decodeRle([0, 2], 4, 3) ?? [])).toEqual([1, 1, ...new Array(10).fill(0)]);
  });

  it('truncates runs that overshoot the box', () => {
    expect(Array.from(decodeRle([0, 9999], 4, 3) ?? [])).toEqual(new Array(12).fill(1));
  });

  // Reading it as zero would shift every pixel after it, painting a mask that is wrong
  // rather than one that is missing.
  it('refuses a negative run instead of guessing', () => {
    expect(decodeRle([0, -4], 4, 3)).toBeNull();
    expect(decodeRle([0, Number.NaN], 4, 3)).toBeNull();
  });

  it('refuses a box with no area', () => {
    expect(decodeRle([0, 1], 0, 3)).toBeNull();
    expect(decodeRle([0, 1], 4, -1)).toBeNull();
    expect(decodeRle([0, 1], 4.5, 3)).toBeNull();
  });
});

describe('decodeMask', () => {
  it('reads a stored mask', () => {
    const decoded = decodeMask(lMask());
    expect(decoded?.left).toBe(10);
    expect(decoded?.width).toBe(4);
    expect(Array.from(decoded?.flags ?? [])).toEqual(L_FLAGS);
  });

  it('is null for anything it cannot read, rather than an empty mask at the origin', () => {
    expect(decodeMask(undefined)).toBeNull();
    expect(decodeMask(null)).toBeNull();
    expect(decodeMask({ rle: [0, 2], left: 0, top: 0, width: 0, height: 2 })).toBeNull();
    expect(
      decodeMask({ rle: [0, 2], left: Number.NaN, top: 0, width: 2, height: 2 }),
    ).toBeNull();
    expect(decodeMask({ rle: 'nope', left: 0, top: 0, width: 2, height: 2 } as never)).toBeNull();
  });

  it('moves every pixel when the box moves', () => {
    expect(maskContains(lMask(100, 200), 100, 200)).toBe(true);
    expect(maskContains(lMask(100, 200), 10, 10)).toBe(false);
  });
});

describe('maskContains', () => {
  it('is false for a pixel inside the box but outside the mask', () => {
    // (13, 10) is the box's top-right corner and the L does not reach it.
    expect(maskContains(lMask(), 13, 10)).toBe(false);
    expect(maskContains(lMask(), 12, 12)).toBe(false);
  });

  it('is false outside the box entirely', () => {
    expect(maskContains(lMask(), 9, 10)).toBe(false);
    expect(maskContains(lMask(), 10, 9)).toBe(false);
    expect(maskContains(lMask(), 14, 10)).toBe(false);
  });

  it('floors a fractional coordinate onto its pixel', () => {
    expect(maskContains(lMask(), 10.9, 10.9)).toBe(true);
    expect(maskContains(lMask(), 13.9, 10.1)).toBe(false);
  });
});

describe('maskRgba', () => {
  it('writes the colour only where the mask covers', () => {
    const decoded = decodeMask(lMask());
    expect(decoded).not.toBeNull();
    const bytes = maskRgba(decoded!, [255, 0, 0], 1);

    // Pixel 0 is covered; pixel 3 (the box's top-right) is not.
    expect(Array.from(bytes.slice(0, 4))).toEqual([255, 0, 0, 255]);
    expect(Array.from(bytes.slice(12, 16))).toEqual([0, 0, 0, 0]);
  });

  it('is four bytes per pixel of the box', () => {
    const decoded = decodeMask(lMask())!;
    expect(maskRgba(decoded, [1, 2, 3], 0.5)).toHaveLength(4 * 12);
  });

  it('clamps the alpha rather than wrapping it', () => {
    const decoded = decodeMask(lMask())!;
    expect(maskRgba(decoded, [1, 2, 3], 5)[3]).toBe(255);
    expect(maskRgba(decoded, [1, 2, 3], -1)[3]).toBe(0);
  });
});

describe('rgbOf', () => {
  it('reads both hex lengths', () => {
    expect(rgbOf('#ef4444')).toEqual([239, 68, 68]);
    expect(rgbOf('#fff')).toEqual([255, 255, 255]);
  });

  it('falls back rather than producing NaN channels', () => {
    expect(rgbOf('rebeccapurple')).toEqual([56, 189, 248]);
    expect(rgbOf('')).toEqual([56, 189, 248]);
  });
});

describe('a mask in the scene', () => {
  it('is bounded by its own box, not by whatever points it carries', () => {
    // `points` deliberately disagrees: an importer or a tool may not have set it, and the
    // runs are the geometry.
    const annotation = maskAnnotation(lMask(), [0, 0, 1, 1]);
    expect(annotationBounds(annotation)).toEqual({ minX: 10, minY: 10, maxX: 13, maxY: 12 });
  });

  it('falls back to its points when the mask is unreadable', () => {
    const annotation = maskAnnotation(undefined, [4, 5, 8, 9]);
    expect(annotationBounds(annotation)).toEqual({ minX: 4, minY: 5, maxX: 8, maxY: 9 });
  });

  // Picking by the bounding box makes a thin diagonal mask swallow clicks across a large
  // empty area and sit on top of whatever is really there.
  it('is picked where it covers and not where it merely spans', () => {
    const annotation = maskAnnotation(lMask());
    expect(hitTest(annotation, { x: 11, y: 10 }, 0)).toBe(true);
    expect(hitTest(annotation, { x: 13, y: 12 }, 0)).toBe(false);
  });

  it('is not picked outside its box however generous the tolerance', () => {
    const annotation = maskAnnotation(lMask());
    expect(hitTest(annotation, { x: 40, y: 40 }, 6)).toBe(false);
  });

  // An annotator clicking a visible mask and selecting nothing is the failure that matters
  // more than the occasional generous pick.
  it('is reachable from just outside its edge, within tolerance', () => {
    const annotation = maskAnnotation(lMask());
    expect(hitTest(annotation, { x: 13, y: 10 }, 0)).toBe(false);
    expect(hitTest(annotation, { x: 13, y: 10 }, 2)).toBe(true);
  });

  it('falls back to the box when there is no mask to consult', () => {
    const annotation = maskAnnotation(undefined, [10, 10, 13, 12]);
    expect(hitTest(annotation, { x: 13, y: 12 }, 0)).toBe(true);
  });
});

// -------------------------------------------------------------------- painting a stroke
//
// What a mask brush commits with. A 6x6 flat buffer keeps every worked example small
// enough to read as a grid in a comment.

function grid(flags: Uint8Array, width: number, height: number): string[] {
  const rows: string[] = [];
  for (let y = 0; y < height; y += 1) {
    rows.push(
      Array.from({ length: width }, (_, x) => (flags[y * width + x] ? '#' : '.')).join(''),
    );
  }
  return rows;
}

describe('paintDisc', () => {
  it('paints a filled circle centred on the given point', () => {
    const flags = new Uint8Array(6 * 6);
    paintDisc(flags, 6, 6, 2.5, 2.5, 1.5, 1);
    expect(grid(flags, 6, 6)).toEqual([
      '......',
      '.###..',
      '.###..',
      '.###..',
      '......',
      '......',
    ]);
  });

  it('clips to the buffer rather than reading or writing outside it', () => {
    const flags = new Uint8Array(4 * 4);
    // Centred one cell outside the top-left corner; must not throw, wrap around, or paint
    // the whole buffer as a side effect of the clipped loop bounds being wrong.
    expect(() => paintDisc(flags, 4, 4, -1, -1, 3, 1)).not.toThrow();
    expect(flags[0]).toBe(1); // (0,0) is within the radius of a centre just off the corner
    expect(flags[3 * 4 + 3]).toBe(0); // the far corner is not
  });

  it('erases by painting 0 over what is already there', () => {
    const flags = new Uint8Array(6 * 6).fill(1);
    paintDisc(flags, 6, 6, 2.5, 2.5, 1.5, 0);
    expect(grid(flags, 6, 6)).toEqual([
      '######',
      '#...##',
      '#...##',
      '#...##',
      '######',
      '######',
    ]);
  });
});

describe('paintStroke', () => {
  it('leaves a continuous line rather than dots with gaps between them', () => {
    const flags = new Uint8Array(20 * 4);
    paintStroke(flags, 20, 4, { x: 1, y: 1.5 }, { x: 18, y: 1.5 }, 1, 1);
    // A gap would show up as a `.` inside the run between the two ends.
    const row = grid(flags, 20, 4)[1] ?? '';
    const span = row.slice(1, 19);
    expect(span).not.toContain('.');
  });

  it('paints something even when the two ends are the same point', () => {
    const flags = new Uint8Array(6 * 6);
    paintStroke(flags, 6, 6, { x: 3, y: 3 }, { x: 3, y: 3 }, 1, 1);
    expect(flags[3 * 6 + 3]).toBe(1);
  });
});

describe('paintedBounds', () => {
  it('is null for a buffer with nothing painted', () => {
    expect(paintedBounds(new Uint8Array(4 * 4), 4, 4)).toBeNull();
  });

  it('is the smallest box containing every set pixel', () => {
    const flags = new Uint8Array(6 * 6);
    flags[1 * 6 + 2] = 1;
    flags[4 * 6 + 5] = 1;
    expect(paintedBounds(flags, 6, 6)).toEqual([2, 1, 5, 4]);
  });

  it('is a single point for one painted pixel', () => {
    const flags = new Uint8Array(6 * 6);
    flags[3 * 6 + 3] = 1;
    expect(paintedBounds(flags, 6, 6)).toEqual([3, 3, 3, 3]);
  });
});

describe('cropFlags', () => {
  it('extracts exactly the sub-rectangle, row-major', () => {
    // A 4x3 buffer with a 2x2 block at (1,1)..(2,2).
    const flags = new Uint8Array(4 * 3);
    for (const [x, y] of [
      [1, 1],
      [2, 1],
      [1, 2],
      [2, 2],
    ] as const) {
      flags[y * 4 + x] = 1;
    }
    const cropped = cropFlags(flags, 4, [1, 1, 2, 2]);
    expect(Array.from(cropped)).toEqual([1, 1, 1, 1]);
  });

  // What `encodeRle(cropFlags(...))` has to agree with `decodeRle` about: the crop is the
  // exact box a mask's own `left/top/width/height` will describe.
  it('round-trips through encodeRle back to the original box', () => {
    const flags = new Uint8Array(6 * 6);
    paintDisc(flags, 6, 6, 3, 3, 1.5, 1);
    const box = paintedBounds(flags, 6, 6);
    expect(box).not.toBeNull();
    const [minX, minY, maxX, maxY] = box!;
    const cropped = cropFlags(flags, 6, box!);
    const rle = encodeRle(cropped);
    const decoded = decodeRle(rle, maxX - minX + 1, maxY - minY + 1);
    expect(Array.from(decoded!)).toEqual(Array.from(cropped));
  });
});

describe('paintMaskInto', () => {
  it('stamps a stored mask at its absolute position in a frame-sized buffer', () => {
    const flags = new Uint8Array(20 * 15);
    paintMaskInto(flags, 20, lMask(10, 10));
    // `lMask` is the shared L fixture at (10, 10): (10,10), (11,10), (12,10), (10,11).
    expect(flags[10 * 20 + 10]).toBe(1);
    expect(flags[10 * 20 + 11]).toBe(1);
    expect(flags[10 * 20 + 12]).toBe(1);
    expect(flags[11 * 20 + 10]).toBe(1);
    expect(flags[11 * 20 + 11]).toBe(0);
  });

  it('does nothing for a missing or malformed mask, leaving the buffer as it was', () => {
    const flags = new Uint8Array(4 * 4);
    paintMaskInto(flags, 4, null);
    paintMaskInto(flags, 4, undefined);
    expect(Array.from(flags)).toEqual(new Array(16).fill(0));
  });

  // What lets a brush stroke edit a mask rather than only draw a new one: seeding the
  // buffer, then erasing part of it, has to act on the real shape rather than on blank.
  it('is what a brush edit paints over before erasing or adding to it', () => {
    const flags = new Uint8Array(20 * 15);
    paintMaskInto(flags, 20, lMask(10, 10));
    paintDisc(flags, 20, 15, 10.5, 10.5, 0.5, 0); // erase just the top-left corner
    expect(flags[10 * 20 + 10]).toBe(0);
    expect(flags[10 * 20 + 11]).toBe(1); // its neighbour is untouched
  });
});
