/**
 * Fetching frames a chunk at a time.
 *
 * The claim this makes is a *request-count* claim — 36 frames should cost one request, not
 * 36 — so these tests count requests. A cache that produced the right pixels while still
 * fetching per frame would pass a pixel test and fail the point.
 *
 * The other half is that it must always be safe to give up. Every way a chunk can fail —
 * an unreadable archive, a network error, a frame the archive does not contain — has to end
 * in `null`, which is the caller's signal to use the single-frame endpoint. A cache that
 * threw instead would take the editor down over an optimisation.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '@/api/client';
import { ChunkCache, chunkOf, chunkable, frameEntryName } from '../chunks';

const FRAMES_PER_CHUNK = 4;

/** A real stored ZIP holding `frames`, each with distinguishable bytes. */
function archiveFor(frames: number[]): ArrayBuffer {
  const encoder = new TextEncoder();
  const locals: Uint8Array[] = [];
  const centrals: Uint8Array[] = [];
  let offset = 0;

  for (const frame of frames) {
    const name = encoder.encode(frameEntryName(frame));
    const data = new Uint8Array([0xff, 0xd8, 0xff, frame & 0xff]);

    const local = new Uint8Array(30 + name.length + data.length);
    const lv = new DataView(local.buffer);
    lv.setUint32(0, 0x04034b50, true);
    lv.setUint32(18, data.length, true);
    lv.setUint32(22, data.length, true);
    lv.setUint16(26, name.length, true);
    local.set(name, 30);
    local.set(data, 30 + name.length);
    locals.push(local);

    const central = new Uint8Array(46 + name.length);
    const cv = new DataView(central.buffer);
    cv.setUint32(0, 0x02014b50, true);
    cv.setUint32(20, data.length, true);
    cv.setUint32(24, data.length, true);
    cv.setUint16(28, name.length, true);
    cv.setUint32(42, offset, true);
    central.set(name, 46);
    centrals.push(central);

    offset += local.length;
  }

  const centralSize = centrals.reduce((total, part) => total + part.length, 0);
  const end = new Uint8Array(22);
  const ev = new DataView(end.buffer);
  ev.setUint32(0, 0x06054b50, true);
  ev.setUint16(8, frames.length, true);
  ev.setUint16(10, frames.length, true);
  ev.setUint32(12, centralSize, true);
  ev.setUint32(16, offset, true);

  const archive = new Uint8Array(offset + centralSize + end.length);
  let cursor = 0;
  for (const part of [...locals, ...centrals, end]) {
    archive.set(part, cursor);
    cursor += part.length;
  }
  return archive.buffer;
}

function framesOfChunk(index: number): number[] {
  const start = index * FRAMES_PER_CHUNK;
  return [start, start + 1, start + 2, start + 3];
}

let requested: number[];

beforeEach(() => {
  requested = [];
  vi.spyOn(api, 'chunkArchive').mockImplementation(async (_task, chunk) => {
    requested.push(chunk);
    return archiveFor(framesOfChunk(chunk));
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

// --------------------------------------------------------------------------- the maths

describe('chunkOf', () => {
  it('maps a frame to the chunk holding it', () => {
    expect(chunkOf(0, 4)).toBe(0);
    expect(chunkOf(3, 4)).toBe(0);
    expect(chunkOf(4, 4)).toBe(1);
    expect(chunkOf(399, 36)).toBe(11);
  });

  it('says -1 when chunking is off, rather than dividing by zero', () => {
    expect(chunkOf(10, 0)).toBe(-1);
  });
});

describe('frameEntryName', () => {
  it('matches what the server writes into the archive', () => {
    // Six digits, zero-padded, task-global. If this and the server ever disagree, every
    // lookup misses silently and the editor quietly falls back to per-frame fetching.
    expect(frameEntryName(0)).toBe('000000.jpg');
    expect(frameEntryName(7)).toBe('000007.jpg');
    expect(frameEntryName(123456)).toBe('123456.jpg');
  });
});

describe('chunkable', () => {
  const meta = {
    task_id: 't',
    media_kind: 'video' as const,
    frame_count: 100,
    frames_per_chunk: 36,
    chunk_count: 3,
  };

  it('accepts a video task with chunks', () => {
    expect(chunkable(meta)).toBe(true);
  });

  it('declines everything that would make a chunk pointless or impossible', () => {
    expect(chunkable(undefined)).toBe(false);
    expect(chunkable({ ...meta, media_kind: 'image' })).toBe(false);
    expect(chunkable({ ...meta, frames_per_chunk: 0 })).toBe(false);
    expect(chunkable({ ...meta, chunk_count: 0 })).toBe(false);
  });
});

// ------------------------------------------------------------------- the request count

describe('ChunkCache', () => {
  it('fetches one archive for a whole chunk of frames', async () => {
    const cache = new ChunkCache('task-1', FRAMES_PER_CHUNK);

    for (const frame of [0, 1, 2, 3]) {
      expect(await cache.frame(frame)).not.toBeNull();
    }
    expect(requested).toEqual([0]);
  });

  it('fetches the next archive when the annotator crosses a boundary', async () => {
    const cache = new ChunkCache('task-1', FRAMES_PER_CHUNK);
    await cache.frame(3);
    await cache.frame(4);
    expect(requested).toEqual([0, 1]);
  });

  it('serves a cached frame synchronously, with no request and no await', async () => {
    const cache = new ChunkCache('task-1', FRAMES_PER_CHUNK);
    await cache.frame(0);
    requested.length = 0;

    // This is the common case while stepping, and it has to avoid a render with no image.
    expect(cache.cached(2)).not.toBeNull();
    expect(requested).toEqual([]);
  });

  it('returns the right frame, not merely a frame', async () => {
    const cache = new ChunkCache('task-1', FRAMES_PER_CHUNK);
    const fifth = await cache.frame(5);
    expect(fifth?.[3]).toBe(5);
  });

  it('shares one request between two frames of the same chunk asked for at once', async () => {
    const cache = new ChunkCache('task-1', FRAMES_PER_CHUNK);
    await Promise.all([cache.frame(0), cache.frame(1), cache.frame(2)]);
    expect(requested).toEqual([0]);
  });

  it('prefetches the neighbouring chunks so a boundary does not stall', async () => {
    const cache = new ChunkCache('task-1', FRAMES_PER_CHUNK);
    cache.prefetch(5);
    await vi.waitFor(() => expect(requested.length).toBe(3));
    expect([...requested].sort()).toEqual([0, 1, 2]);
  });

  it('does not prefetch a chunk it already holds', async () => {
    const cache = new ChunkCache('task-1', FRAMES_PER_CHUNK);
    await cache.frame(0);
    requested.length = 0;

    cache.prefetch(0);
    await vi.waitFor(() => expect(requested.length).toBe(1));
    expect(requested).toEqual([1]); // chunk 0 is held; -1 does not exist
  });

  // ----------------------------------------------------------------------- the LRU

  it('evicts the least recently used archive rather than growing forever', async () => {
    const cache = new ChunkCache('task-1', FRAMES_PER_CHUNK, 2);
    await cache.frame(0); // chunk 0
    await cache.frame(4); // chunk 1
    await cache.frame(8); // chunk 2 — evicts chunk 0

    expect(cache.cached(0)).toBeNull();
    expect(cache.cached(4)).not.toBeNull();
    expect(cache.cached(8)).not.toBeNull();
  });

  it('counts a read as a use, so stepping back and forth does not evict what is in use', async () => {
    const cache = new ChunkCache('task-1', FRAMES_PER_CHUNK, 2);
    await cache.frame(0); // chunk 0
    await cache.frame(4); // chunk 1
    cache.cached(0); // chunk 0 used again — chunk 1 is now the oldest
    await cache.frame(8); // chunk 2

    expect(cache.cached(0)).not.toBeNull();
    expect(cache.cached(4)).toBeNull();
  });

  // --------------------------------------------------- giving up is always an option

  it('gives up quietly when the archive cannot be read', async () => {
    vi.spyOn(api, 'chunkArchive').mockResolvedValue(
      new TextEncoder().encode('not a zip').buffer as ArrayBuffer,
    );
    const cache = new ChunkCache('task-1', FRAMES_PER_CHUNK);
    expect(await cache.frame(0)).toBeNull();
  });

  it('does not ask again for an archive it could not read', async () => {
    const attempts: number[] = [];
    vi.spyOn(api, 'chunkArchive').mockImplementation(async (_task, chunk) => {
      attempts.push(chunk);
      return new TextEncoder().encode('not a zip').buffer as ArrayBuffer;
    });

    const cache = new ChunkCache('task-1', FRAMES_PER_CHUNK);
    // Otherwise one bad chunk becomes a failed request per keystroke.
    for (const frame of [0, 1, 2, 3]) await cache.frame(frame);
    expect(attempts).toEqual([0]);
  });

  it('gives up quietly when the request fails, and stays willing to retry', async () => {
    let calls = 0;
    vi.spyOn(api, 'chunkArchive').mockImplementation(async (_task, chunk) => {
      calls += 1;
      if (calls === 1) throw new Error('network');
      return archiveFor(framesOfChunk(chunk));
    });

    const cache = new ChunkCache('task-1', FRAMES_PER_CHUNK);
    expect(await cache.frame(0)).toBeNull(); // falls back to the single frame
    expect(await cache.frame(0)).not.toBeNull(); // a blip is not permanent
  });

  it('gives up on a frame the archive does not contain', async () => {
    // A mixed task has gaps: chunks hold video frames, and an image asset in the range
    // contributes none.
    vi.spyOn(api, 'chunkArchive').mockResolvedValue(archiveFor([1, 2]));
    const cache = new ChunkCache('task-1', FRAMES_PER_CHUNK);
    expect(await cache.frame(0)).toBeNull();
    expect(await cache.frame(1)).not.toBeNull();
  });

  it('never fetches when chunking is switched off', async () => {
    const cache = new ChunkCache('task-1', 0);
    expect(await cache.frame(7)).toBeNull();
    cache.prefetch(7);
    expect(requested).toEqual([]);
  });
});
