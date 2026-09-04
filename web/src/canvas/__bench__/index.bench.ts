/**
 * Annotation engine benchmarks.
 *
 * `npm run bench`. These exist so the performance claims in the docs can be checked rather
 * than believed, and so a regression in the spatial index shows up as a number.
 *
 * The headline claim under test: with viewport culling, the work per frame tracks what is
 * *on screen*, not what is in the dataset. The 1k / 10k / 100k viewport-query benchmarks
 * should stay within the same order of magnitude of each other.
 */

import { bench, describe } from 'vitest';
import { SpatialIndex } from '../spatial';
import { hitTest } from '../geometry';
import { interpolateAcrossScales, makeAnnotations } from './fixtures';

const SCALES = [1_000, 10_000, 100_000] as const;

describe('index build', () => {
  for (const count of SCALES) {
    const annotations = makeAnnotations(count);
    bench(`bulk load ${count.toLocaleString()}`, () => {
      new SpatialIndex().reset(annotations);
    });
  }
});

describe('viewport query (the culling hot path)', () => {
  // One 1920x1080 viewport over a dataset spread across a large canvas. If these three
  // numbers diverge sharply, culling has stopped working.
  const viewport = { minX: 0, minY: 0, maxX: 1920, maxY: 1080 };
  for (const count of SCALES) {
    const index = new SpatialIndex();
    index.reset(makeAnnotations(count));
    bench(`query ${count.toLocaleString()}`, () => {
      index.search(viewport);
    });
  }
});

describe('picking', () => {
  for (const count of SCALES) {
    const index = new SpatialIndex();
    index.reset(makeAnnotations(count));
    bench(`pick from ${count.toLocaleString()}`, () => {
      index.pick({ x: 640, y: 480 }, 6);
    });
  }
});

describe('incremental edits', () => {
  const index = new SpatialIndex();
  const annotations = makeAnnotations(10_000);
  index.reset(annotations);
  let cursor = 0;

  bench('update one shape in a 10,000-shape index', () => {
    const annotation = annotations[cursor % annotations.length]!;
    cursor += 1;
    index.update({ ...annotation, points: annotation.points.map((v) => v + 1) });
  });
});

describe('hit testing', () => {
  const polygon = makeAnnotations(1, 'polygon')[0]!;
  bench('polygon hit test', () => {
    hitTest(polygon, { x: 20, y: 20 }, 4);
  });

  const rectangle = makeAnnotations(1, 'rectangle')[0]!;
  bench('rectangle hit test', () => {
    hitTest(rectangle, { x: 20, y: 20 }, 4);
  });
});

describe('linear scan (the approach culling replaces)', () => {
  // Kept as a control: this is what picking costs without a spatial index, and it is why
  // the index is not optional.
  for (const count of [1_000, 10_000] as const) {
    const annotations = makeAnnotations(count);
    bench(`scan ${count.toLocaleString()}`, () => {
      annotations.filter((a) => hitTest(a, { x: 640, y: 480 }, 6));
    });
  }
});

describe('track interpolation', () => {
  bench('10,000 frame probes over 1,000 keyframes', () => {
    interpolateAcrossScales(1_000, 10_000);
  });
});
