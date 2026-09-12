/**
 * A frame's pixels, as a URL the canvas can draw.
 *
 * The media endpoint enforces the same permission check as the rest of the API — media is
 * never served from a public bucket — so it needs an `Authorization` header. An `<img>`
 * element cannot send one: `crossOrigin` controls cookies, not bearer tokens. Fetching the
 * bytes and wrapping them in an object URL is what lets an authenticated endpoint feed an
 * image element at all.
 *
 * For a video task the bytes come from a **chunk**: the server decodes 36 frames in one
 * pass and serves them as one archive, so stepping through frames costs one request per
 * chunk rather than one per frame. Everything about that is best-effort — an image task,
 * chunking switched off, an unreadable archive or a failed request all fall through to the
 * single-frame endpoint, which always works.
 *
 * Object URLs are revoked as soon as they are replaced. An annotator scrubbing through a
 * few thousand frames would otherwise pin every one of them in memory for the lifetime of
 * the document, which the browser will not reclaim on its own.
 */

import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/api/client';
import { ChunkCache, chunkable } from './chunks';

export function useFrameObjectUrl(taskId: string | undefined, frame: number): string | null {
  const [url, setUrl] = useState<string | null>(null);

  // Cheap, cached by the query client, and needed before any chunk can be asked for. A
  // failure here simply means no chunking, not no frames.
  const media = useQuery({
    queryKey: ['task-media', taskId],
    queryFn: () => api.taskMedia(taskId as string),
    enabled: Boolean(taskId),
    staleTime: Infinity,
    retry: false,
  });

  const cache = useMemo(() => {
    if (!taskId || !chunkable(media.data)) return null;
    return new ChunkCache(taskId, media.data.frames_per_chunk);
    // A new cache per task, and per chunk size. Not per frame: the whole point is that it
    // outlives the frame that filled it.
  }, [taskId, media.data]);

  useEffect(() => {
    if (!taskId) {
      setUrl(null);
      return;
    }

    const controller = new AbortController();
    let objectUrl: string | null = null;
    let cancelled = false;

    const show = (blob: Blob) => {
      if (cancelled) return;
      objectUrl = URL.createObjectURL(blob);
      setUrl(objectUrl);
    };

    // Synchronous when the chunk is already in memory, which is the common case while
    // stepping: no await, no flicker, no request.
    const ready = cache?.cached(frame);
    if (ready) {
      show(new Blob([ready as BlobPart], { type: 'image/jpeg' }));
    } else {
      void (async () => {
        try {
          const fromChunk = await cache?.frame(frame, controller.signal);
          if (cancelled) return;
          if (fromChunk) {
            show(new Blob([fromChunk as BlobPart], { type: 'image/jpeg' }));
            return;
          }
          show(await api.frameBlob(taskId, frame, controller.signal));
        } catch {
          // A frame that cannot be fetched leaves the canvas empty rather than showing a
          // stale picture from the previous frame, which would be worse than blank: an
          // annotation drawn on it would be anchored to the wrong image.
          if (!cancelled) setUrl(null);
        }
      })();
    }

    // Warm the neighbouring chunk so crossing a boundary does not stall. Deliberately not
    // awaited and not cancelled: it outlives this frame on purpose.
    cache?.prefetch(frame);

    return () => {
      cancelled = true;
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [taskId, frame, cache]);

  return url;
}
