/**
 * Describing the result of a local folder import.
 *
 * Pure, and separate from the component, because the honest reporting is the part worth
 * testing: the server attaches what it can and lists what it could not, so "imported 2,999
 * files" on its own is a half-truth when one was corrupt. Whatever this returns is what the
 * user is told happened.
 */

import type { LocalImportResult } from '@/api/types';

export interface ImportSummary {
  /** One line, always. Never empty, including when nothing at all was imported. */
  headline: string;
  /** The per-file failures, verbatim from the server. Empty when everything worked. */
  skipped: string[];
  tone: 'success' | 'warning';
}

function plural(count: number, noun: string): string {
  return `${count.toLocaleString()} ${noun}${count === 1 ? '' : 's'}`;
}

/**
 * Combine several sequential imports into one result `summariseImport` can describe.
 *
 * Choosing individual files, unlike a folder, means one server call per file — the
 * endpoint takes a single path, and calling it concurrently for the same task would race
 * on the position each file is assigned. Without this, five chosen files would flash five
 * separate notices past the user instead of reading as the one action they took.
 *
 * `frame_count` is the task's running total as of each call, not a per-call delta, so only
 * the *last* result's is still true once every file has landed — an earlier one is a
 * correct snapshot of a moment that has already passed. Callers must not pass an empty
 * list; there is no result to summarise before anything has been attempted.
 */
export function mergeImportResults(results: readonly LocalImportResult[]): LocalImportResult {
  const last = results[results.length - 1];
  if (!last) {
    throw new Error('mergeImportResults: nothing to merge — at least one result is required');
  }
  return {
    task_id: last.task_id,
    imported: results.flatMap((result) => result.imported),
    skipped: results.flatMap((result) => result.skipped),
    frame_count: last.frame_count,
  };
}

export function summariseImport(result: LocalImportResult): ImportSummary {
  const imported = result.imported.length;
  const skipped = result.skipped;

  if (imported === 0) {
    return {
      // A folder of holiday snaps in HEIC produces exactly this, and "0 files imported"
      // with no reason is the least useful thing we could say about it.
      headline:
        skipped.length > 0
          ? `Nothing was imported. ${plural(skipped.length, 'file')} could not be read.`
          : 'No media found in that folder.',
      skipped,
      tone: 'warning',
    };
  }

  const headline =
    skipped.length > 0
      ? `Imported ${plural(imported, 'file')}; skipped ${plural(skipped.length, 'file')}. ` +
        `This task now has ${plural(result.frame_count, 'frame')}.`
      : `Imported ${plural(imported, 'file')}. ` +
        `This task now has ${plural(result.frame_count, 'frame')}.`;

  return { headline, skipped, tone: skipped.length > 0 ? 'warning' : 'success' };
}
