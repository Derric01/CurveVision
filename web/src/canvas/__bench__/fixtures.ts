/** Deterministic fixtures for the engine benchmarks. */

import type { Annotation, ShapeType } from '../types';

/** A tiny deterministic PRNG, so benchmark runs are comparable across machines. */
function mulberry32(seed: number): () => number {
  let state = seed;
  return () => {
    state |= 0;
    state = (state + 0x6d2b79f5) | 0;
    let t = Math.imul(state ^ (state >>> 15), 1 | state);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function makeAnnotations(count: number, shapeType: ShapeType = 'rectangle'): Annotation[] {
  const random = mulberry32(count);
  const annotations: Annotation[] = [];

  for (let i = 0; i < count; i++) {
    // Spread over a canvas much larger than one viewport, which is what makes the culling
    // benchmark meaningful.
    const x = random() * 20_000;
    const y = random() * 12_000;
    const w = 20 + random() * 80;
    const h = 20 + random() * 80;

    const points =
      shapeType === 'polygon'
        ? [x, y, x + w, y, x + w, y + h, x + w / 2, y + h * 1.2, x, y + h]
        : [x, y, x + w, y + h];

    annotations.push({
      id: `bench-${i}`,
      labelId: `label-${i % 8}`,
      frame: 0,
      shapeType,
      points,
      rotation: 0,
      occluded: false,
      outside: false,
      zOrder: i % 4,
      source: 'manual',
      attributes: {},
    });
  }
  return annotations;
}

/** Linear interpolation over a synthetic track, mirroring the server's algorithm. */
export function interpolateAcrossScales(keyframes: number, probes: number): number {
  const frames = new Float64Array(keyframes * 5);
  for (let i = 0; i < keyframes; i++) {
    frames[i * 5] = i * 10;
    frames[i * 5 + 1] = i;
    frames[i * 5 + 2] = i;
    frames[i * 5 + 3] = i + 20;
    frames[i * 5 + 4] = i + 15;
  }

  let checksum = 0;
  for (let probe = 0; probe < probes; probe++) {
    const index = Math.min(keyframes - 2, Math.floor(probe / 10));
    const startFrame = frames[index * 5]!;
    const endFrame = frames[(index + 1) * 5]!;
    const span = endFrame - startFrame || 1;
    const t = (probe - startFrame) / span;
    for (let coordinate = 1; coordinate <= 4; coordinate++) {
      const a = frames[index * 5 + coordinate]!;
      const b = frames[(index + 1) * 5 + coordinate]!;
      checksum += a + (b - a) * t;
    }
  }
  return checksum;
}
