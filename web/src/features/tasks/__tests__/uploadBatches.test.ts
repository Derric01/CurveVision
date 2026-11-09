import { describe, expect, it } from 'vitest';
import { planUploadBatches, uploadInBatches } from '../uploadBatches';

const file = (name: string, size = 1_000) => ({ name, size });
const many = (count: number, size = 1_000) =>
  Array.from({ length: count }, (_, i) => file(`img_${i}.jpg`, size));

describe('planUploadBatches', () => {
  it('splits 10,000 photos into requests of at most 100, in order', () => {
    const files = many(10_000);
    const batches = planUploadBatches(files);
    expect(batches).toHaveLength(100);
    expect(batches.every((batch) => batch.length === 100)).toBe(true);
    expect(batches.flat()).toEqual(files);
  });

  it('closes a batch before it goes over the byte limit', () => {
    const batches = planUploadBatches(many(5, 30), 100, 64);
    expect(batches.map((batch) => batch.length)).toEqual([2, 2, 1]);
  });

  it('sends a file bigger than the limit on its own rather than dropping it', () => {
    const batches = planUploadBatches([file('a', 10), file('huge', 500), file('b', 10)], 100, 64);
    expect(batches.map((batch) => batch.map((f) => f.name))).toEqual([['a'], ['huge'], ['b']]);
  });

  it('plans nothing for no files', () => {
    expect(planUploadBatches([])).toEqual([]);
  });
});

describe('uploadInBatches', () => {
  it('reports progress after every batch and every file as uploaded', async () => {
    const sent: number[] = [];
    const progress: number[] = [];
    const entries = await uploadInBatches(
      many(250),
      async (batch) => {
        sent.push(batch.length);
      },
      (done) => progress.push(done),
    );
    expect(sent).toEqual([100, 100, 50]);
    expect(progress).toEqual([100, 200, 250]);
    expect(entries.every((entry) => entry.ok)).toBe(true);
  });

  it('retries a failed batch once, then reports it and carries on', async () => {
    let calls = 0;
    const entries = await uploadInBatches(many(300), async () => {
      calls += 1;
      // The second batch fails both times; everything else succeeds.
      if (calls === 2 || calls === 3) throw new Error('connection reset');
    });
    expect(calls).toBe(4);
    expect(entries.filter((entry) => !entry.ok)).toHaveLength(100);
    expect(entries.find((entry) => !entry.ok)?.reason).toBe('connection reset');
    expect(entries.filter((entry) => entry.ok)).toHaveLength(200);
  });

  it('a batch that succeeds on the retry counts as uploaded', async () => {
    let calls = 0;
    const entries = await uploadInBatches(many(10), async () => {
      calls += 1;
      if (calls === 1) throw new Error('timeout');
    });
    expect(calls).toBe(2);
    expect(entries.every((entry) => entry.ok)).toBe(true);
  });
});
