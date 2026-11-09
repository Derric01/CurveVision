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

import { test } from 'vitest';

import { SpatialIndex } from '../spatial';
import { hitTest as hitTestExport } from '../geometry';
import { interpolateAcrossScales as interpolateExport, makeAnnotations } from './fixtures';

// Bound once: calling through a module export getter inside a benchmark loop measures the
// getter as well as the function (https://vitest.dev/guide/benchmarking#module-runner-overhead).
const hitTest = hitTestExport;
const interpolateAcrossScales = interpolateExport;

const SCALES = [1_000, 10_000, 100_000] as const;

// Vitest 5 runs benchmarks inside a test, through its `bench` fixture; `compare` runs each
// group's registrations together and reports them side by side.

test('index build', async ({ bench }) => {
  await bench.compare(
    ...SCALES.map((count) => {
      const annotations = makeAnnotations(count);
      return bench(`bulk load ${count.toLocaleString()}`, () => {
        new SpatialIndex().reset(annotations);
      });
    }),
  );
});

test('viewport query (the culling hot path)', async ({ bench }) => {
  // One 1920x1080 viewport over a dataset spread across a large canvas. If these three
  // numbers diverge sharply, culling has stopped working.
  const viewport = { minX: 0, minY: 0, maxX: 1920, maxY: 1080 };
  await bench.compare(
    ...SCALES.map((count) => {
      const index = new SpatialIndex();
      index.reset(makeAnnotations(count));
      return bench(`query ${count.toLocaleString()}`, () => {
        index.search(viewport);
      });
    }),
  );
});

test('picking', async ({ bench }) => {
  await bench.compare(
    ...SCALES.map((count) => {
      const index = new SpatialIndex();
      index.reset(makeAnnotations(count));
      return bench(`pick from ${count.toLocaleString()}`, () => {
        index.pick({ x: 640, y: 480 }, 6);
      });
    }),
  );
});

test('incremental edits', async ({ bench }) => {
  const index = new SpatialIndex();
  const annotations = makeAnnotations(10_000);
  index.reset(annotations);
  let cursor = 0;

  await bench('update one shape in a 10,000-shape index', () => {
    const annotation = annotations[cursor % annotations.length]!;
    cursor += 1;
    index.update({ ...annotation, points: annotation.points.map((v) => v + 1) });
  }).run();
});

test('hit testing', async ({ bench }) => {
  const polygon = makeAnnotations(1, 'polygon')[0]!;
  const rectangle = makeAnnotations(1, 'rectangle')[0]!;
  await bench.compare(
    bench('polygon hit test', () => {
      hitTest(polygon, { x: 20, y: 20 }, 4);
    }),
    bench('rectangle hit test', () => {
      hitTest(rectangle, { x: 20, y: 20 }, 4);
    }),
  );
});

test('linear scan (the approach culling replaces)', async ({ bench }) => {
  // Kept as a control: this is what picking costs without a spatial index, and it is why
  // the index is not optional.
  //
  // Every scale the index is measured at, so the comparison is like for like. Quoting
  // `pick from 100,000` against `scan 10,000` — as the docs once did — compares different
  // datasets and flatters the index by an order of magnitude it did not earn.
  await bench.compare(
    ...SCALES.map((count) => {
      const annotations = makeAnnotations(count);
      return bench(`scan ${count.toLocaleString()}`, () => {
        annotations.filter((a) => hitTest(a, { x: 640, y: 480 }, 6));
      });
    }),
  );
});

test('track interpolation', async ({ bench }) => {
  await bench('10,000 frame probes over 1,000 keyframes', () => {
    interpolateAcrossScales(1_000, 10_000);
  }).run();
});
