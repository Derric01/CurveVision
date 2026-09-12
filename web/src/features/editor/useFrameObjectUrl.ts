/**
 * A frame's pixels, as a URL the canvas can draw.
 *
 * The media endpoint enforces the same permission check as the rest of the API — media is
 * never served from a public bucket — so it needs an `Authorization` header. An `<img>`
 * element cannot send one: `crossOrigin` controls cookies, not bearer tokens. Fetching the
 * bytes and wrapping them in an object URL is what lets an authenticated endpoint feed an
 * image element at all.
 *
 * Object URLs are revoked as soon as they are replaced. An annotator scrubbing through a
 * few thousand frames would otherwise pin every one of them in memory for the lifetime of
 * the document, which the browser will not reclaim on its own.
 */

import { useEffect, useState } from 'react';
import { api } from '@/api/client';

export function useFrameObjectUrl(taskId: string | undefined, frame: number): string | null {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!taskId) {
      setUrl(null);
      return;
    }

    const controller = new AbortController();
    let objectUrl: string | null = null;
    let cancelled = false;

    void api
      .frameBlob(taskId, frame, controller.signal)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      })
      .catch(() => {
        // A frame that cannot be fetched leaves the canvas empty rather than showing a
        // stale picture from the previous frame, which would be worse than blank: an
        // annotation drawn on it would be anchored to the wrong image.
        if (!cancelled) setUrl(null);
      });

    return () => {
      cancelled = true;
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [taskId, frame]);

  return url;
}
