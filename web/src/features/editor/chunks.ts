/**
 * Frames, fetched a chunk at a time.
 *
 * The server decodes a run of video frames in one pass and serves them as one archive, so
 * an annotator stepping through frames should be making one request per 36 frames rather
 * than one per frame. This is the client half of that: a small LRU of archives, a lookup
 * that answers from memory when it can, and a prefetch of the neighbouring chunk so
 * crossing a boundary does not stall.
 *
 * **It is always optional.** Every path here can decline — an image task, chunking switched
 * off server-side, an archive in a format this cannot read, a request that failed — and the
 * caller falls back to fetching the single frame. A frame must never fail to appear because
 * an optimisation did not work out.
 */

import { api } from '@/api/client';
import type { TaskMediaMeta } from '@/api/types';
import { UnsupportedArchiveError, readStoredZip, type ArchiveEntries } from '@/media/storedZip';

/**
 * How many archives to keep. Three covers the annotator's actual movement — the chunk they
 * are in, the one they just left, and the one being prefetched — while bounding memory at
 * roughly three chunks of JPEG, which for 36 frames of 1080p is tens of megabytes rather
 * than the hundreds that keeping everything would reach.
 */
export const CACHE_SIZE = 3;

export function chunkOf(frame: number, framesPerChunk: number): number {
  return framesPerChunk > 0 ? Math.floor(frame / framesPerChunk) : -1;
}

/** The name the server gives a frame inside an archive. Task-global, zero-padded to six. */
export function frameEntryName(frame: number): string {
  return `${String(frame).padStart(6, '0')}.jpg`;
}

/** Whether chunks are worth using for this task at all. */
export function chunkable(meta: TaskMediaMeta | undefined): meta is TaskMediaMeta {
  return (
    meta !== undefined &&
    meta.media_kind === 'video' &&
    meta.frames_per_chunk > 0 &&
    meta.chunk_count > 0
  );
}

export class ChunkCache {
  /** Insertion order is the LRU order; a hit re-inserts to move the entry to the end. */
  private readonly entries = new Map<number, ArchiveEntries>();
  private readonly inFlight = new Map<number, Promise<ArchiveEntries | null>>();
  /**
   * Chunks that returned something unusable. Retrying them on every frame would turn one
   * failure into a request per keystroke, and the per-frame fallback already works.
   */
  private readonly refused = new Set<number>();

  constructor(
    private readonly taskId: string,
    private readonly framesPerChunk: number,
    private readonly size: number = CACHE_SIZE,
  ) {}

  /** A frame's JPEG from an archive already in memory, or `null`. Never fetches. */
  cached(frame: number): Uint8Array | null {
    const index = chunkOf(frame, this.framesPerChunk);
    const archive = this.entries.get(index);
    if (archive === undefined) return null;
    // Re-insert so the LRU sees this as the most recent use.
    this.entries.delete(index);
    this.entries.set(index, archive);
    return archive.get(frameEntryName(frame)) ?? null;
  }

  /**
   * A frame's JPEG, fetching its chunk if necessary. `null` means "use the single-frame
   * endpoint" — an unreadable archive, a failed request, or a chunk that simply does not
   * contain this frame (a mixed task has gaps).
   */
  async frame(frame: number, signal?: AbortSignal): Promise<Uint8Array | null> {
    const hit = this.cached(frame);
    if (hit !== null) return hit;

    const index = chunkOf(frame, this.framesPerChunk);
    if (index < 0 || this.refused.has(index)) return null;

    const archive = await this.load(index, signal);
    return archive?.get(frameEntryName(frame)) ?? null;
  }

  /**
   * Warm the chunk `frame` sits in, and its neighbours. Fire-and-forget: a prefetch that
   * fails costs nothing, because the frame path re-decides for itself.
   */
  prefetch(frame: number): void {
    const index = chunkOf(frame, this.framesPerChunk);
    if (index < 0) return;
    for (const candidate of [index, index + 1, index - 1]) {
      if (candidate < 0 || this.entries.has(candidate) || this.refused.has(candidate)) continue;
      void this.load(candidate).catch(() => undefined);
    }
  }

  private load(index: number, signal?: AbortSignal): Promise<ArchiveEntries | null> {
    // Two frames of the same chunk requested together must share one request, not race.
    const existing = this.inFlight.get(index);
    if (existing !== undefined) return existing;

    const pending = (async (): Promise<ArchiveEntries | null> => {
      try {
        const buffer = await api.chunkArchive(this.taskId, index, signal);
        const archive = readStoredZip(buffer);
        this.remember(index, archive);
        return archive;
      } catch (error) {
        // An archive this cannot read is a permanent no for that chunk; a network failure
        // or an abort is not, and stays retryable.
        if (error instanceof UnsupportedArchiveError) this.refused.add(index);
        return null;
      } finally {
        this.inFlight.delete(index);
      }
    })();

    this.inFlight.set(index, pending);
    return pending;
  }

  private remember(index: number, archive: ArchiveEntries): void {
    this.entries.set(index, archive);
    while (this.entries.size > this.size) {
      const oldest = this.entries.keys().next();
      if (oldest.done) break;
      this.entries.delete(oldest.value);
    }
  }
}
