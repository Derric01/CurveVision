/**
 * Ordinary photos, uploaded in batches.
 *
 * Picking 10,000 photos used to send all of them in one request: tens of gigabytes that a
 * dropped connection threw away whole, past the server's request-size limit, with no
 * progress to show. Batches bound every one of those: a request is at most `maxFiles` files
 * and `maxBytes` bytes, a failed batch is retried once and then reported file by file
 * while the rest carry on, and progress moves after every batch.
 *
 * Batches go one at a time, not in parallel. Each upload appends at the task's next asset
 * position, and two landing at once would race for it -- the same reason large files go
 * through the resumable path sequentially.
 */

import type { UploadEntry } from './resumableUpload';

export const UPLOAD_BATCH_MAX_FILES = 100;
export const UPLOAD_BATCH_MAX_BYTES = 64 * 1024 * 1024;

/** Split `files` into batches, keeping their order; a file over `maxBytes` goes alone. */
export function planUploadBatches<T extends Pick<File, 'size'>>(
  files: readonly T[],
  maxFiles: number = UPLOAD_BATCH_MAX_FILES,
  maxBytes: number = UPLOAD_BATCH_MAX_BYTES,
): T[][] {
  const batches: T[][] = [];
  let current: T[] = [];
  let bytes = 0;
  for (const file of files) {
    if (current.length > 0 && (current.length >= maxFiles || bytes + file.size > maxBytes)) {
      batches.push(current);
      current = [];
      bytes = 0;
    }
    current.push(file);
    bytes += file.size;
  }
  if (current.length > 0) batches.push(current);
  return batches;
}

/**
 * Upload `files` with `send`, one batch at a time.
 *
 * `onProgress(done, total)` fires after every batch, failed ones included, so a bar always
 * reaches the end. Never throws: every file comes back as an entry, `ok` or with a reason.
 */
export async function uploadInBatches<T extends Pick<File, 'name' | 'size'>>(
  files: readonly T[],
  send: (batch: T[]) => Promise<unknown>,
  onProgress?: (done: number, total: number) => void,
  options: { maxFiles?: number; maxBytes?: number } = {},
): Promise<UploadEntry[]> {
  const entries: UploadEntry[] = [];
  let done = 0;
  for (const batch of planUploadBatches(files, options.maxFiles, options.maxBytes)) {
    let failure: unknown = null;
    for (let attempt = 0; attempt < 2; attempt += 1) {
      try {
        await send(batch);
        failure = null;
        break;
      } catch (error) {
        failure = error;
      }
    }
    if (failure === null) {
      entries.push(...batch.map((file) => ({ name: file.name, ok: true })));
    } else {
      const reason = failure instanceof Error ? failure.message : 'upload failed';
      entries.push(...batch.map((file) => ({ name: file.name, ok: false, reason })));
    }
    done += batch.length;
    onProgress?.(done, files.length);
  }
  return entries;
}
