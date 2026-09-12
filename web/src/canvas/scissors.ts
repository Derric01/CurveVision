// The shape of this tool -- anchor on click, live preview to the cursor, backspace to unwind
// one anchor, and a downscaled search grid -- follows CVAT's
// `cvat-core/src/opencv/intelligent-scissors.ts`, at commit
// 1d0c39576c3239dcaf8ba7baee71a1b8de496c0e.
//
//   Copyright (C) 2021-2022 Intel Corporation
//   Copyright (C) CVAT.ai Corporation
//   SPDX-License-Identifier: MIT
//
// Copyright (C) CurveVision contributors, for the implementation.
// SPDX-License-Identifier: MIT
//
// No algorithm is taken from there, because it contains none: those 196 lines delegate to
// OpenCV.js. What follows is the published live-wire algorithm implemented directly. See
// `docs/adr/0009-intelligent-scissors.md`.

/**
 * Intelligent scissors: a boundary that snaps to the edge under the cursor.
 *
 * Tracing a curved object by clicking vertices is the slowest thing in manual annotation and
 * the least accurate — a leaf, a road sign, a person's coat. This finds the edge for you:
 * click once to anchor, and the wire from that anchor to your cursor follows the strongest
 * boundary between them, live, as you move.
 *
 * The algorithm is **live-wire** (Mortensen & Barrett, *Intelligent Scissors for Image
 * Composition*, SIGGRAPH 1995). Every pixel is a node; the cost of stepping from one pixel to
 * a neighbour is low along an edge and high across flat ground. Dijkstra from the anchor
 * gives a shortest-path tree over the whole image, after which the path to *any* cursor
 * position is a pointer-walk back up that tree. That asymmetry is the whole design: the
 * expensive work happens once per click, and cursor movement is free.
 *
 * Three costs are summed, following the paper's weights:
 *
 * | Term | What it wants | Weight |
 * | --- | --- | --- |
 * | Laplacian zero-crossing | land exactly *on* the edge, not one pixel beside it | 0.43 |
 * | Gradient magnitude | prefer strong edges to weak ones | 0.43 |
 * | Gradient direction | keep going the way the edge goes, rather than cutting corners | 0.14 |
 *
 * Dropping the direction term is tempting — it is the fiddly one — and it is what makes the
 * difference between a wire that hugs a curve and one that shortcuts across it whenever two
 * edges pass close together.
 *
 * **Not** a port of OpenCV's `IntelligentScissorsMB`, which is what CVAT loads a ~10 MB WASM
 * build for. This is the published algorithm implemented directly, so the editor keeps its
 * no-dependency rule and the desktop build does not grow by an order of magnitude for one
 * tool. See `docs/adr/0009-intelligent-scissors.md`.
 */

/** Longest side of the grid the search runs on. */
export const DEFAULT_WORKING_SIZE = 1024;

/** Weights from the paper. They sum to 1, so a link cost lands in [0, 1] before distance. */
const WEIGHT_ZERO_CROSSING = 0.43;
const WEIGHT_GRADIENT = 0.43;
const WEIGHT_DIRECTION = 0.14;

/** Link costs are bucketed as integers; this is how finely. */
const COST_SCALE = 1024;

const SQRT2 = Math.SQRT2;
const TWO_OVER_3PI = 2 / (3 * Math.PI);

/**
 * `acos` over a quantised dot product.
 *
 * The direction term needs two `Math.acos` per link, and the search evaluates eight links per
 * pixel over the whole grid — about 4.7 million calls per click at 1024×576, which measured
 * as the dominant cost by a wide margin. The input is a dot product of unit vectors, so it is
 * bounded in [-1, 1] and a 4096-entry table is accurate to ~0.0005 rad: far below the
 * resolution at which the cost is then rounded to an integer bucket.
 */
const ACOS_TABLE_SIZE = 4096;
const ACOS_TABLE = new Float32Array(ACOS_TABLE_SIZE + 1);
for (let i = 0; i <= ACOS_TABLE_SIZE; i++) {
  ACOS_TABLE[i] = Math.acos((i / ACOS_TABLE_SIZE) * 2 - 1);
}

function fastAcos(value: number): number {
  const clamped = value < -1 ? -1 : value > 1 ? 1 : value;
  return ACOS_TABLE[((clamped + 1) * 0.5 * ACOS_TABLE_SIZE + 0.5) | 0] ?? Math.acos(clamped);
}

export interface Point {
  x: number;
  y: number;
}

export interface ScissorsOptions {
  /** Longest side of the internal grid. Smaller is faster and blunter. */
  workingSize?: number;
}

/**
 * Per-pixel edge features, computed once per image.
 *
 * Separated from the search because it is the expensive half that does not depend on where
 * the user clicked: changing the anchor re-runs Dijkstra, not this.
 */
export interface EdgeFeatures {
  width: number;
  height: number;
  /** Gradient magnitude, normalised to [0, 1]. */
  magnitude: Float32Array;
  /** Unit vector *along* the edge (perpendicular to the gradient). */
  edgeX: Float32Array;
  edgeY: Float32Array;
  /** 0 where the Laplacian crosses zero (an edge), 1 elsewhere. */
  zeroCrossing: Float32Array;
  /** Scale from image pixels to this grid. */
  scaleX: number;
  scaleY: number;
}

/** Luminance, as the eye weights it. Edges in colour images live mostly here. */
function luminance(data: Uint8ClampedArray, index: number): number {
  return (
    0.2126 * (data[index] ?? 0) + 0.7152 * (data[index + 1] ?? 0) + 0.0722 * (data[index + 2] ?? 0)
  );
}

/**
 * Downsample to the working grid with box averaging.
 *
 * Averaging rather than nearest-neighbour matters: point sampling aliases a hard edge into a
 * staircase, and the gradient of a staircase points in the wrong direction every other pixel,
 * which is exactly what the direction cost is reading.
 */
export function toGrayscale(
  image: ImageData,
  workingSize = DEFAULT_WORKING_SIZE,
): { gray: Float32Array; width: number; height: number; scaleX: number; scaleY: number } {
  const longest = Math.max(image.width, image.height);
  const ratio = longest > workingSize ? workingSize / longest : 1;
  const width = Math.max(1, Math.round(image.width * ratio));
  const height = Math.max(1, Math.round(image.height * ratio));
  const gray = new Float32Array(width * height);

  const stepX = image.width / width;
  const stepY = image.height / height;

  for (let y = 0; y < height; y++) {
    const y0 = Math.floor(y * stepY);
    const y1 = Math.max(y0 + 1, Math.floor((y + 1) * stepY));
    for (let x = 0; x < width; x++) {
      const x0 = Math.floor(x * stepX);
      const x1 = Math.max(x0 + 1, Math.floor((x + 1) * stepX));
      let total = 0;
      let count = 0;
      for (let sy = y0; sy < y1 && sy < image.height; sy++) {
        for (let sx = x0; sx < x1 && sx < image.width; sx++) {
          total += luminance(image.data, (sy * image.width + sx) * 4);
          count++;
        }
      }
      gray[y * width + x] = count > 0 ? total / count : 0;
    }
  }

  return { gray, width, height, scaleX: width / image.width, scaleY: height / image.height };
}

/** Sobel gradients, the Laplacian, and its zero crossings. */
export function computeFeatures(
  image: ImageData,
  options: ScissorsOptions = {},
): EdgeFeatures {
  const { gray, width, height, scaleX, scaleY } = toGrayscale(
    image,
    options.workingSize ?? DEFAULT_WORKING_SIZE,
  );
  const size = width * height;
  const magnitude = new Float32Array(size);
  const edgeX = new Float32Array(size);
  const edgeY = new Float32Array(size);
  const laplacian = new Float32Array(size);
  const zeroCrossing = new Float32Array(size).fill(1);

  const at = (x: number, y: number): number => {
    const cx = x < 0 ? 0 : x >= width ? width - 1 : x;
    const cy = y < 0 ? 0 : y >= height ? height - 1 : y;
    return gray[cy * width + cx] ?? 0;
  };

  let maxMagnitude = 0;
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const index = y * width + x;
      const tl = at(x - 1, y - 1);
      const tc = at(x, y - 1);
      const tr = at(x + 1, y - 1);
      const ml = at(x - 1, y);
      const mc = at(x, y);
      const mr = at(x + 1, y);
      const bl = at(x - 1, y + 1);
      const bc = at(x, y + 1);
      const br = at(x + 1, y + 1);

      const gx = tr + 2 * mr + br - (tl + 2 * ml + bl);
      const gy = bl + 2 * bc + br - (tl + 2 * tc + tr);
      const m = Math.sqrt(gx * gx + gy * gy);
      magnitude[index] = m;
      if (m > maxMagnitude) maxMagnitude = m;

      // The edge runs perpendicular to the gradient. Storing it this way lets the direction
      // cost be two dot products rather than a pile of trigonometry per link.
      if (m > 0) {
        edgeX[index] = gy / m;
        edgeY[index] = -gx / m;
      }

      laplacian[index] = tc + ml + mr + bc - 4 * mc;
    }
  }

  // Normalise and invert: a strong edge must be *cheap*, so the cost is 1 - magnitude.
  if (maxMagnitude > 0) {
    for (let i = 0; i < size; i++) {
      magnitude[i] = 1 - (magnitude[i] ?? 0) / maxMagnitude;
    }
  } else {
    magnitude.fill(1);
  }

  // A zero crossing is a **sign change** between neighbours; the pixel closer to zero is the
  // one sitting on the edge. Marking both sides would make the edge two pixels wide and let
  // the wire wobble between them.
  //
  // An exactly-zero Laplacian is not by itself an edge — flat ground is full of them, and
  // treating those as edges makes empty space cheap to cross, which is the failure where the
  // wire stops tracking the boundary and wanders. A zero pixel counts only when the pixels on
  // either side of it disagree in sign, which is a crossing that happens to land dead centre.
  const sign = (value: number): number => (value > 0 ? 1 : value < 0 ? -1 : 0);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const index = y * width + x;
      const here = laplacian[index] ?? 0;

      if (here === 0) {
        const horizontal =
          x > 0 && x + 1 < width && sign(laplacian[index - 1] ?? 0) * sign(laplacian[index + 1] ?? 0) < 0;
        const vertical =
          y > 0 &&
          y + 1 < height &&
          sign(laplacian[index - width] ?? 0) * sign(laplacian[index + width] ?? 0) < 0;
        if (horizontal || vertical) zeroCrossing[index] = 0;
        continue;
      }

      for (const [dx, dy] of [
        [1, 0],
        [0, 1],
      ] as const) {
        const nx = x + dx;
        const ny = y + dy;
        if (nx >= width || ny >= height) continue;
        const neighbour = laplacian[ny * width + nx] ?? 0;
        if (here * neighbour < 0) {
          if (Math.abs(here) <= Math.abs(neighbour)) zeroCrossing[index] = 0;
          else zeroCrossing[ny * width + nx] = 0;
        }
      }
    }
  }

  return { width, height, magnitude, edgeX, edgeY, zeroCrossing, scaleX, scaleY };
}

const NEIGHBOURS: ReadonlyArray<readonly [number, number]> = [
  [-1, -1],
  [0, -1],
  [1, -1],
  [-1, 0],
  [1, 0],
  [-1, 1],
  [0, 1],
  [1, 1],
];

/**
 * The cost of stepping from `from` to `to`, as an integer for the bucket queue.
 *
 * Exported because it is the part worth testing directly: whether a step along an edge really
 * is cheaper than a step across one is the claim the whole tool rests on.
 */
export function linkCost(features: EdgeFeatures, from: number, to: number, dx: number, dy: number): number {
  const distance = dx !== 0 && dy !== 0 ? SQRT2 : 1;

  const zero = features.zeroCrossing[to] ?? 1;
  const gradient = features.magnitude[to] ?? 1;

  // Direction: how far the step deviates from the edge running through both pixels. `link`
  // is flipped where necessary so the comparison is always with the acute angle -- an edge
  // has no inherent direction, only an orientation, and penalising a wire for tracing a
  // boundary "backwards" would be meaningless.
  const inverseLength = 1 / distance;
  let linkX = dx * inverseLength;
  let linkY = dy * inverseLength;
  const fromEdgeX = features.edgeX[from] ?? 0;
  const fromEdgeY = features.edgeY[from] ?? 0;
  const toEdgeX = features.edgeX[to] ?? 0;
  const toEdgeY = features.edgeY[to] ?? 0;

  if (fromEdgeX * linkX + fromEdgeY * linkY < 0) {
    linkX = -linkX;
    linkY = -linkY;
  }
  const dotFrom = fromEdgeX * linkX + fromEdgeY * linkY;
  const dotTo = toEdgeX * linkX + toEdgeY * linkY;
  const direction = TWO_OVER_3PI * (fastAcos(dotFrom) + fastAcos(dotTo < 0 ? -dotTo : dotTo));

  const local =
    WEIGHT_ZERO_CROSSING * zero + WEIGHT_GRADIENT * gradient + WEIGHT_DIRECTION * direction;
  return Math.max(0, Math.round(local * distance * COST_SCALE));
}

/** Highest integer cost a single link can carry, which sizes the bucket queue. */
const MAX_LINK_COST = Math.ceil(SQRT2 * COST_SCALE) + 1;

/**
 * A live wire anchored at one point.
 *
 * The search is **lazy**: `setAnchor` only seeds it, and `pathTo` expands the frontier just
 * far enough to settle the pixel asked for, keeping its queue between calls. Building the
 * whole tree up front measured at ~200 ms on a 1024×576 grid — a visible hitch on every
 * click, and nearly all of it wasted, because an annotator's next click is usually tens of
 * pixels along the boundary rather than across the image. Expanding on demand moves that
 * cost to the cursor, where it is paid in proportion to how far the cursor actually went,
 * and a nearby move costs almost nothing.
 *
 * Correctness survives the laziness because Dijkstra settles nodes in non-decreasing cost
 * order: a pixel popped from the queue already has its final distance, so pausing and
 * resuming the loop cannot change any answer it has already given.
 */
export class LiveWire {
  private readonly features: EdgeFeatures;
  /** Index of each pixel's predecessor on the cheapest path back to the anchor. */
  private readonly previous: Int32Array;
  private readonly total: Float64Array;
  private readonly visited: Uint8Array;
  private anchorIndex = -1;

  /** Frontier state, persisted across `pathTo` calls so the search can resume. */
  private buckets: number[][] = [];
  private queued = 0;
  private cursor = 0;

  constructor(features: EdgeFeatures) {
    this.features = features;
    const size = features.width * features.height;
    this.previous = new Int32Array(size).fill(-1);
    this.total = new Float64Array(size);
    this.visited = new Uint8Array(size);
  }

  get gridSize(): { width: number; height: number } {
    return { width: this.features.width, height: this.features.height };
  }

  /** Convert a point in image pixels to an index on the working grid. */
  private toIndex(point: Point): number {
    const { width, height, scaleX, scaleY } = this.features;
    const x = Math.min(width - 1, Math.max(0, Math.round(point.x * scaleX)));
    const y = Math.min(height - 1, Math.max(0, Math.round(point.y * scaleY)));
    return y * width + x;
  }

  private toPoint(index: number): Point {
    const { width, scaleX, scaleY } = this.features;
    return { x: (index % width) / scaleX, y: Math.floor(index / width) / scaleY };
  }

  /** Seed the search at `anchor`. Cheap: the work happens in `pathTo`. */
  setAnchor(anchor: Point): void {
    const start = this.toIndex(anchor);
    this.anchorIndex = start;
    this.previous.fill(-1);
    this.total.fill(Infinity);
    this.visited.fill(0);

    // Circular buckets (Dial's algorithm) rather than a binary heap: link costs are bounded
    // small integers, so buckets give amortised O(1) per pop where a heap gives O(log n).
    // Every relaxation lands within MAX_LINK_COST of the current distance, so that many
    // buckets keep the queue monotone.
    this.buckets = Array.from({ length: MAX_LINK_COST + 1 }, () => []);
    this.cursor = 0;
    this.queued = 1;
    this.total[start] = 0;
    this.buckets[0]!.push(start);
  }

  /**
   * Expand the frontier until `target` is settled, or the grid is exhausted.
   *
   * Returns whether the target ended up with a known shortest path.
   */
  private expandTo(target: number): boolean {
    if (this.visited[target]) return true;
    const { width, height } = this.features;
    const bucketCount = this.buckets.length;

    while (this.queued > 0) {
      const bucket = this.buckets[this.cursor % bucketCount]!;
      if (bucket.length === 0) {
        this.cursor++;
        continue;
      }
      const index = bucket.pop()!;
      this.queued--;
      if (this.visited[index]) continue;
      // A node can sit in several buckets after being relaxed more than once; the first pop
      // is the cheapest, so later copies are stale and skipped by the visited flag.
      if ((this.total[index] ?? Infinity) !== this.cursor) continue;
      this.visited[index] = 1;
      if (index === target) return true;

      const x = index % width;
      const y = (index - x) / width;
      const base = this.total[index] ?? 0;

      for (const [dx, dy] of NEIGHBOURS) {
        const nx = x + dx;
        const ny = y + dy;
        if (nx < 0 || ny < 0 || nx >= width || ny >= height) continue;
        const neighbour = ny * width + nx;
        if (this.visited[neighbour]) continue;

        const candidate = base + linkCost(this.features, index, neighbour, dx, dy);
        if (candidate < (this.total[neighbour] ?? Infinity)) {
          this.total[neighbour] = candidate;
          this.previous[neighbour] = index;
          this.buckets[candidate % bucketCount]!.push(neighbour);
          this.queued++;
        }
      }
    }
    return this.visited[target] === 1;
  }

  /** How much of the grid the search has settled so far. Exposed for tests. */
  get settledCount(): number {
    let count = 0;
    for (let i = 0; i < this.visited.length; i++) count += this.visited[i] ?? 0;
    return count;
  }

  get hasAnchor(): boolean {
    return this.anchorIndex >= 0;
  }

  /**
   * The cheapest boundary from the anchor to `target`, in image pixels.
   *
   * Returned anchor-first. An empty array means no anchor has been set.
   */
  pathTo(target: Point): Point[] {
    if (this.anchorIndex < 0) return [];
    let index = this.toIndex(target);
    this.expandTo(index);
    const reversed: Point[] = [];
    // The guard is a belt-and-braces stop: a correct tree cannot cycle, but a wrong one would
    // hang the editor rather than draw a bad line, and that is the worse failure.
    const limit = this.features.width * this.features.height;
    let steps = 0;
    while (index >= 0 && steps++ < limit) {
      reversed.push(this.toPoint(index));
      if (index === this.anchorIndex) break;
      index = this.previous[index] ?? -1;
    }
    if (index !== this.anchorIndex) {
      // Unreachable: the target is outside the tree. Fall back to a straight line so the tool
      // still does something predictable rather than nothing.
      return [this.toPoint(this.anchorIndex), { x: target.x, y: target.y }];
    }
    return reversed.reverse();
  }
}

/**
 * Drop points that sit on a near-straight run.
 *
 * The wire is pixel-dense: a 400-pixel boundary arrives as 400 vertices, which is unusable as
 * a polygon — slow to render, slow to edit, and enormous in an export. Ramer–Douglas–Peucker
 * keeps the corners and discards the rest.
 */
export function simplify(points: Point[], tolerance: number): Point[] {
  if (points.length <= 2 || tolerance <= 0) return [...points];

  const keep = new Uint8Array(points.length);
  keep[0] = 1;
  keep[points.length - 1] = 1;
  const stack: Array<[number, number]> = [[0, points.length - 1]];

  while (stack.length > 0) {
    const [first, last] = stack.pop()!;
    if (last <= first + 1) continue;
    const a = points[first]!;
    const b = points[last]!;
    let farthest = -1;
    let maxDistance = tolerance;
    for (let i = first + 1; i < last; i++) {
      const distance = perpendicularDistance(points[i]!, a, b);
      if (distance > maxDistance) {
        maxDistance = distance;
        farthest = i;
      }
    }
    if (farthest > 0) {
      keep[farthest] = 1;
      stack.push([first, farthest], [farthest, last]);
    }
  }

  return points.filter((_, index) => keep[index] === 1);
}

function perpendicularDistance(point: Point, a: Point, b: Point): number {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const lengthSquared = dx * dx + dy * dy;
  if (lengthSquared === 0) return Math.hypot(point.x - a.x, point.y - a.y);
  let t = ((point.x - a.x) * dx + (point.y - a.y) * dy) / lengthSquared;
  t = t < 0 ? 0 : t > 1 ? 1 : t;
  return Math.hypot(point.x - (a.x + t * dx), point.y - (a.y + t * dy));
}
