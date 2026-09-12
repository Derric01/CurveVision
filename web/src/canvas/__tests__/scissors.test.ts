/**
 * Does the wire actually follow the edge?
 *
 * That is the only question worth asking about this tool, and it is easy to write tests that
 * do not ask it. A path from A to B always exists and always looks plausible in a debugger;
 * the test that means something is one where the correct answer is *not* a straight line, so
 * an implementation that quietly ignored the image would fail.
 *
 * Every image here is built so the right answer is known in advance and is visibly not the
 * shortest route.
 */

import { describe, expect, it } from 'vitest';

import {
  computeFeatures,
  linkCost,
  LiveWire,
  simplify,
  toGrayscale,
  type Point,
} from '../scissors';

/** A blank canvas. `paint` decides the luminance at each pixel. */
function image(
  width: number,
  height: number,
  paint: (x: number, y: number) => number,
): ImageData {
  const data = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const value = paint(x, y);
      const index = (y * width + x) * 4;
      data[index] = value;
      data[index + 1] = value;
      data[index + 2] = value;
      data[index + 3] = 255;
    }
  }
  // Node has no ImageData; the code reads only these three fields.
  return { data, width, height, colorSpace: 'srgb' } as ImageData;
}

/**
 * A dark band that bulges upward in the middle — the shape that makes this test honest.
 *
 * The straight line between the two ends cuts clean across the bulge, so any path that
 * follows the band must be measurably longer and higher than that line.
 */
function bulgingEdge(width = 120, height = 80): { image: ImageData; edgeY: (x: number) => number } {
  const edgeY = (x: number): number => {
    const t = x / (width - 1);
    return Math.round(60 - 34 * Math.sin(Math.PI * t));
  };
  return {
    image: image(width, height, (x, y) => (Math.abs(y - edgeY(x)) <= 1 ? 0 : 255)),
    edgeY,
  };
}

function maxDeviation(path: Point[], expected: (x: number) => number): number {
  let worst = 0;
  for (const point of path) {
    const x = Math.round(point.x);
    if (x < 0) continue;
    worst = Math.max(worst, Math.abs(point.y - expected(x)));
  }
  return worst;
}

describe('grayscale reduction', () => {
  it('keeps an image that is already small', () => {
    const { width, height, scaleX } = toGrayscale(image(40, 30, () => 128), 1024);
    expect([width, height]).toEqual([40, 30]);
    expect(scaleX).toBe(1);
  });

  it('scales the longest side down and preserves aspect ratio', () => {
    const { width, height } = toGrayscale(image(2000, 1000, () => 0), 500);
    expect(width).toBe(500);
    expect(height).toBe(250);
  });

  it('averages rather than point-samples when downscaling', () => {
    // Alternating columns average to mid grey. Nearest-neighbour would return 0 or 255, and
    // the gradient of an aliased edge points the wrong way every other pixel.
    const { gray } = toGrayscale(image(64, 4, (x) => (x % 2 === 0 ? 0 : 255)), 8);
    for (const value of gray) {
      expect(value).toBeGreaterThan(100);
      expect(value).toBeLessThan(160);
    }
  });
});

describe('edge features', () => {
  it('marks a hard edge as cheap and flat ground as expensive', () => {
    const features = computeFeatures(image(40, 40, (_x, y) => (y < 20 ? 0 : 255)));
    const index = (x: number, y: number): number => y * features.width + x;

    // `magnitude` is stored inverted: low means a strong edge.
    expect(features.magnitude[index(20, 20)]!).toBeLessThan(0.2);
    expect(features.magnitude[index(20, 5)]!).toBeGreaterThan(0.9);
  });

  it('puts the zero crossing on the edge', () => {
    const features = computeFeatures(image(40, 40, (_x, y) => (y < 20 ? 0 : 255)));
    const column = 20;
    const onEdge = [19, 20].some((y) => features.zeroCrossing[y * features.width + column] === 0);
    expect(onEdge).toBe(true);
    expect(features.zeroCrossing[5 * features.width + column]).toBe(1);
  });

  it('reports the edge direction, not the gradient direction', () => {
    // A horizontal edge: the gradient points down, so the edge runs left-to-right.
    const features = computeFeatures(image(40, 40, (_x, y) => (y < 20 ? 0 : 255)));
    const index = 20 * features.width + 20;
    expect(Math.abs(features.edgeX[index]!)).toBeGreaterThan(0.9);
    expect(Math.abs(features.edgeY[index]!)).toBeLessThan(0.2);
  });
});

describe('link cost', () => {
  it('is cheaper along an edge than across it', () => {
    const features = computeFeatures(image(40, 40, (_x, y) => (y < 20 ? 0 : 255)));
    const width = features.width;
    // Find the row the edge actually landed on rather than assuming it.
    let edgeRow = 20;
    for (let y = 15; y < 25; y++) {
      if ((features.magnitude[y * width + 20] ?? 1) < (features.magnitude[edgeRow * width + 20] ?? 1)) {
        edgeRow = y;
      }
    }
    const here = edgeRow * width + 20;

    const along = linkCost(features, here, here + 1, 1, 0);
    const across = linkCost(features, here, here + width * 3, 0, 1);

    expect(along).toBeLessThan(across);
  });

  it('charges a diagonal step more than an orthogonal one on flat ground', () => {
    const features = computeFeatures(image(40, 40, () => 128));
    const width = features.width;
    const here = 20 * width + 20;

    const orthogonal = linkCost(features, here, here + 1, 1, 0);
    const diagonal = linkCost(features, here, here + width + 1, 1, 1);

    expect(diagonal).toBeGreaterThan(orthogonal);
  });
});

describe('the wire', () => {
  it('follows a bulging edge instead of cutting across it', () => {
    const { image: picture, edgeY } = bulgingEdge();
    const wire = new LiveWire(computeFeatures(picture));
    const start = { x: 4, y: edgeY(4) };
    const end = { x: 115, y: edgeY(115) };

    wire.setAnchor(start);
    const path = wire.pathTo(end);

    expect(path.length).toBeGreaterThan(2);
    expect(maxDeviation(path, edgeY)).toBeLessThanOrEqual(3);

    // The straight line between the ends passes ~30px below the crest. A path that ignored
    // the image would sit on it, so this is the assertion that the pixels were read at all.
    const crest = path.find((point) => Math.abs(point.x - 60) < 2);
    expect(crest).toBeDefined();
    expect(crest!.y).toBeLessThan(35);
  });

  it('is anchored where it was told, and ends where it was asked', () => {
    const { image: picture, edgeY } = bulgingEdge();
    const wire = new LiveWire(computeFeatures(picture));
    const start = { x: 4, y: edgeY(4) };
    wire.setAnchor(start);
    const path = wire.pathTo({ x: 115, y: edgeY(115) });

    expect(Math.hypot(path[0]!.x - start.x, path[0]!.y - start.y)).toBeLessThanOrEqual(2);
    const last = path[path.length - 1]!;
    expect(Math.hypot(last.x - 115, last.y - edgeY(115))).toBeLessThanOrEqual(2);
  });

  it('re-anchoring replaces the previous tree rather than adding to it', () => {
    const { image: picture, edgeY } = bulgingEdge();
    const wire = new LiveWire(computeFeatures(picture));

    wire.setAnchor({ x: 4, y: edgeY(4) });
    const first = wire.pathTo({ x: 60, y: edgeY(60) });
    wire.setAnchor({ x: 60, y: edgeY(60) });
    const second = wire.pathTo({ x: 115, y: edgeY(115) });

    expect(first[0]!.x).toBeLessThan(10);
    expect(second[0]!.x).toBeGreaterThan(50);
  });

  it('expands only as far as the cursor asks', () => {
    // The property that makes the tool usable. Building the whole tree on every click
    // measured at ~200ms on a 1024x576 grid, nearly all of it wasted, because an annotator's
    // next click is tens of pixels along the boundary rather than across the image.
    const { image: picture, edgeY } = bulgingEdge(400, 200);
    const features = computeFeatures(picture);
    const total = features.width * features.height;

    const wire = new LiveWire(features);
    wire.setAnchor({ x: 10, y: edgeY(10) });
    expect(wire.settledCount).toBeLessThan(4);

    wire.pathTo({ x: 40, y: edgeY(40) });
    const near = wire.settledCount;
    expect(near).toBeLessThan(total / 4);

    const far = new LiveWire(features);
    far.setAnchor({ x: 10, y: edgeY(10) });
    far.pathTo({ x: 390, y: edgeY(390) });
    expect(far.settledCount).toBeGreaterThan(near);
  });

  it('resuming the search cannot change an answer it already gave', () => {
    // Dijkstra settles nodes in non-decreasing cost order, so pausing and resuming is safe —
    // but that is an argument, and this is the check. A wire walked outward in steps must
    // agree exactly with one asked for each point cold.
    const { image: picture, edgeY } = bulgingEdge();
    const features = computeFeatures(picture);
    const targets = [20, 40, 60, 80, 100, 115].map((x) => ({ x, y: edgeY(x) }));

    const incremental = new LiveWire(features);
    incremental.setAnchor({ x: 4, y: edgeY(4) });
    const stepwise = targets.map((target) => incremental.pathTo(target));

    const cold = targets.map((target) => {
      const fresh = new LiveWire(features);
      fresh.setAnchor({ x: 4, y: edgeY(4) });
      return fresh.pathTo(target);
    });

    expect(stepwise).toEqual(cold);
  });

  it('returns nothing before an anchor is set', () => {
    const wire = new LiveWire(computeFeatures(image(20, 20, () => 0)));
    expect(wire.hasAnchor).toBe(false);
    expect(wire.pathTo({ x: 5, y: 5 })).toEqual([]);
  });

  it('traces a circle rather than chording it', () => {
    const size = 100;
    const radius = 35;
    const centre = size / 2;
    const picture = image(size, size, (x, y) => {
      const distance = Math.hypot(x - centre, y - centre);
      return Math.abs(distance - radius) <= 1.2 ? 0 : 255;
    });
    const wire = new LiveWire(computeFeatures(picture));

    // Quarter turn: from the top of the circle to the right of it.
    wire.setAnchor({ x: centre, y: centre - radius });
    const path = wire.pathTo({ x: centre + radius, y: centre });

    for (const point of path) {
      const distance = Math.hypot(point.x - centre, point.y - centre);
      expect(Math.abs(distance - radius)).toBeLessThanOrEqual(3);
    }
    // A chord would be ~49px; the arc is ~55px. Length alone is a weak signal, so the
    // per-point radius check above is the real one and this guards the degenerate case.
    expect(path.length).toBeGreaterThan(40);
  });

  it('clamps a target outside the image rather than failing', () => {
    // Every pixel on an 8-connected grid is reachable, so the straight-line fallback in
    // `pathTo` is a guard against a corrupt tree rather than a case a user can reach. What a
    // user *can* do is drag the cursor off the image, and that must stay well defined.
    const wire = new LiveWire(computeFeatures(image(40, 40, (_x, y) => (y < 20 ? 0 : 255))));
    wire.setAnchor({ x: 5, y: 20 });
    const path = wire.pathTo({ x: 500, y: -80 });

    expect(path.length).toBeGreaterThan(1);
    for (const point of path) {
      expect(point.x).toBeGreaterThanOrEqual(0);
      expect(point.x).toBeLessThanOrEqual(40);
      expect(point.y).toBeGreaterThanOrEqual(0);
      expect(point.y).toBeLessThanOrEqual(40);
    }
  });

  it('a flat image gives no free ride across empty space', () => {
    // The failure this guards: treating an exactly-zero Laplacian as an edge makes every flat
    // pixel cheap, and the wire stops tracking boundaries at all. On a blank image the cost
    // must come out uniform and high, so the path is the straight line and nothing else.
    const features = computeFeatures(image(60, 60, () => 128));
    expect(Array.from(features.zeroCrossing).every((value) => value === 1)).toBe(true);

    const wire = new LiveWire(features);
    wire.setAnchor({ x: 5, y: 30 });
    const path = wire.pathTo({ x: 55, y: 30 });
    for (const point of path) {
      expect(Math.abs(point.y - 30)).toBeLessThanOrEqual(1);
    }
  });

  it('works on a downscaled grid and reports coordinates in image pixels', () => {
    const { image: picture, edgeY } = bulgingEdge(480, 320);
    const features = computeFeatures(picture, { workingSize: 120 });
    expect(features.width).toBe(120);

    const wire = new LiveWire(features);
    wire.setAnchor({ x: 16, y: edgeY(16) });
    const path = wire.pathTo({ x: 460, y: edgeY(460) });

    // Coordinates must come back in the original resolution, not the working one.
    expect(Math.max(...path.map((point) => point.x))).toBeGreaterThan(400);
    expect(maxDeviation(path, edgeY)).toBeLessThanOrEqual(12);
  });
});

describe('simplify', () => {
  it('reduces a straight run to its endpoints', () => {
    const line = Array.from({ length: 50 }, (_, i) => ({ x: i, y: 10 }));
    expect(simplify(line, 1)).toEqual([
      { x: 0, y: 10 },
      { x: 49, y: 10 },
    ]);
  });

  it('keeps a corner', () => {
    const corner = [
      ...Array.from({ length: 20 }, (_, i) => ({ x: i, y: 0 })),
      ...Array.from({ length: 20 }, (_, i) => ({ x: 19, y: i })),
    ];
    const simplified = simplify(corner, 1);
    expect(simplified.length).toBeGreaterThanOrEqual(3);
    expect(simplified.some((point) => point.x === 19 && point.y === 0)).toBe(true);
  });

  it('never moves a point further than the tolerance from the original line', () => {
    const { edgeY } = bulgingEdge();
    const dense = Array.from({ length: 120 }, (_, x) => ({ x, y: edgeY(x) }));
    const tolerance = 2;
    const simplified = simplify(dense, tolerance);

    expect(simplified.length).toBeLessThan(dense.length / 4);
    // Every discarded point must still lie within tolerance of the kept polyline.
    for (const point of dense) {
      let best = Infinity;
      for (let i = 0; i + 1 < simplified.length; i++) {
        const a = simplified[i]!;
        const b = simplified[i + 1]!;
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const lengthSquared = dx * dx + dy * dy;
        let t = lengthSquared === 0 ? 0 : ((point.x - a.x) * dx + (point.y - a.y) * dy) / lengthSquared;
        t = Math.max(0, Math.min(1, t));
        best = Math.min(best, Math.hypot(point.x - (a.x + t * dx), point.y - (a.y + t * dy)));
      }
      expect(best).toBeLessThanOrEqual(tolerance + 1e-6);
    }
  });

  it('leaves short paths and zero tolerance alone', () => {
    const pair = [
      { x: 0, y: 0 },
      { x: 5, y: 5 },
    ];
    expect(simplify(pair, 5)).toEqual(pair);
    const dense = Array.from({ length: 10 }, (_, i) => ({ x: i, y: 0 }));
    expect(simplify(dense, 0)).toHaveLength(10);
  });
});
