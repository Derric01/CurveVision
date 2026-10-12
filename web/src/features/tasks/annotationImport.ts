/**
 * Importing an annotation archive into a task, from the task page.
 *
 * `POST /tasks/{id}/import` had no caller in the web client: a dataset in COCO, CVAT XML or
 * any other importable format could be brought in only with the API, the SDK or the CLI.
 * Iteration 62 made what it stores faithful (attributes, tracks); this is the way in.
 *
 * Pure, because what is worth pinning is what a person is told: which formats can be
 * imported, what `replace` destroys, and what the import kept and dropped.
 */

import type { AnnotationImportResult, DatasetFormat, Role } from '@/api/types';

/** The formats that read, in the order the server lists them. */
export function importableFormats(formats: readonly DatasetFormat[] | undefined): DatasetFormat[] {
  return (formats ?? []).filter((format) => format.supports_import);
}

/** What `replace` does, said before it is chosen rather than discovered after. */
export const REPLACE_WARNING =
  'Replace deletes every annotation already in this task’s jobs — drawn, reviewed or ' +
  'suggested — before importing. It cannot be undone.';

/** Whether the form can be sent: a format, a file, and an explicit yes to `replace`. */
export function canImport(input: {
  format: string;
  file: File | null;
  policy: 'append' | 'replace';
  replaceConfirmed: boolean;
}): boolean {
  return Boolean(input.format && input.file) && (input.policy === 'append' || input.replaceConfirmed);
}

function count(n: number, one: string, many = `${one}s`): string {
  return `${n.toLocaleString()} ${n === 1 ? one : many}`;
}

/** The import in one sentence: what was stored, where, and what was created. */
export function describeImport(result: AnnotationImportResult): string {
  const stored = [
    count(result.shapes_imported, 'shape'),
    ...(result.tracks_imported ? [count(result.tracks_imported, 'track')] : []),
    ...(result.tags_imported ? [count(result.tags_imported, 'tag')] : []),
  ];
  const what =
    stored.length > 1
      ? `${stored.slice(0, -1).join(', ')} and ${stored[stored.length - 1]}`
      : stored[0]!;
  if (result.frames_matched === 0) {
    return (
      'Nothing was imported: no frame of this task matched the archive. Image formats ' +
      '(COCO, YOLO, Pascal VOC, KITTI) match frames by image file name, so the archive must ' +
      'name the files this task holds; MOTChallenge and CVAT tracks match by frame number.'
    );
  }
  const created = result.labels_created.length
    ? ` New labels added to the project: ${result.labels_created.join(', ')}.`
    : '';
  return `Imported ${what} across ${count(result.frames_matched, 'frame')}.${created}`;
}

/** Ranks the policy engine lets import into a task (`TASK` / `IMPORT` floors at maintainer). */
const CAN_IMPORT: ReadonlySet<Role> = new Set<Role>(['maintainer', 'admin', 'owner']);

/**
 * Whether to offer the panel. An annotator would otherwise meet a form that can only answer
 * 403; a server older than `role` on an organization sends none, and its 403 answers then.
 */
export function canImportInto(role: Role | undefined): boolean {
  return role === undefined || CAN_IMPORT.has(role);
}
