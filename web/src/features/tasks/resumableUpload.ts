/**
 * Deciding which files need the resumable protocol, and remembering a session across a
 * page reload.
 *
 * A handful of photographs are cheaper as one batched request (`api.uploadAssets`) than as
 * several chunked sessions each paying for their own create/complete round trip — the SDK's
 * own `upload()` makes the identical trade for the identical reason. Only a file large
 * enough that restarting it from zero after a dropped connection would actually hurt goes
 * through `api.uploadResumable` instead.
 *
 * Resuming after a reload needs somewhere to keep the session id that survives the reload
 * but the in-memory `File` object does not. `localStorage`, keyed by the file's name and
 * size, is what lets a person who re-selects the same file recognise it as "the one that was
 * half uploaded" rather than starting over — there is nothing more stable to key it by
 * before the first byte has landed.
 */

import { api } from '@/api/client';
import type { Asset } from '@/api/types';

/** Below this, a batch of files is cheaper as one request than as several chunked ones. */
export const RESUMABLE_UPLOAD_THRESHOLD_BYTES = 20 * 1024 * 1024;

export function partitionBySize(
  files: readonly File[],
  threshold: number = RESUMABLE_UPLOAD_THRESHOLD_BYTES,
): { small: File[]; large: File[] } {
  const small: File[] = [];
  const large: File[] = [];
  for (const file of files) {
    (file.size >= threshold ? large : small).push(file);
  }
  return { small, large };
}

const STORAGE_PREFIX = 'curvevision.upload.';

function storageKey(taskId: string, file: Pick<File, 'name' | 'size'>): string {
  return `${STORAGE_PREFIX}${taskId}:${file.name}:${file.size}`;
}

// `localStorage` throws in private-mode Safari and when site data is blocked. Losing the
// pointer only costs a retry starting from zero rather than resuming, never correctness, so
// every access here degrades to "no id remembered" rather than failing the upload.
export function recallUploadId(taskId: string, file: Pick<File, 'name' | 'size'>): string | null {
  try {
    return localStorage.getItem(storageKey(taskId, file));
  } catch {
    return null;
  }
}

export function rememberUploadId(
  taskId: string,
  file: Pick<File, 'name' | 'size'>,
  uploadId: string,
): void {
  try {
    localStorage.setItem(storageKey(taskId, file), uploadId);
  } catch {
    /* best effort */
  }
}

export function forgetUploadId(taskId: string, file: Pick<File, 'name' | 'size'>): void {
  try {
    localStorage.removeItem(storageKey(taskId, file));
  } catch {
    /* best effort */
  }
}

/**
 * Upload one large file through the resumable protocol, picking up a session remembered
 * from an earlier, interrupted attempt if this exact file (by name and size) has one.
 *
 * The remembered id is deliberately *not* cleared on failure: that pointer is what lets the
 * next attempt resume instead of restarting a multi-gigabyte transfer from zero. It is
 * cleared only once the upload actually completes.
 */
export async function uploadLargeFileResumable(
  taskId: string,
  file: File,
  onProgress?: (receivedBytes: number, totalBytes: number) => void,
): Promise<Asset> {
  const uploadId = recallUploadId(taskId, file) ?? undefined;
  const asset = await api.uploadResumable(taskId, file, {
    uploadId,
    onProgress: (received, total, sessionId) => {
      rememberUploadId(taskId, file, sessionId);
      onProgress?.(received, total);
    },
  });
  forgetUploadId(taskId, file);
  return asset;
}

export interface UploadEntry {
  name: string;
  ok: boolean;
  reason?: string;
}

function plural(count: number, noun: string): string {
  return `${count.toLocaleString()} ${noun}${count === 1 ? '' : 's'}`;
}

/** One line describing a batch of uploads, small and large together. */
export function summariseUpload(entries: readonly UploadEntry[]): {
  headline: string;
  tone: 'success' | 'warning';
} {
  if (entries.length === 0) {
    return { headline: 'No files selected.', tone: 'warning' };
  }
  const failed = entries.filter((entry) => !entry.ok);
  const succeeded = entries.length - failed.length;

  if (failed.length === 0) {
    return { headline: `Uploaded ${plural(succeeded, 'file')}.`, tone: 'success' };
  }
  if (succeeded === 0) {
    return { headline: `${plural(failed.length, 'file')} failed to upload.`, tone: 'warning' };
  }
  return {
    headline: `Uploaded ${plural(succeeded, 'file')}; ${plural(failed.length, 'file')} failed.`,
    tone: 'warning',
  };
}
