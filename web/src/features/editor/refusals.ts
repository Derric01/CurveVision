/**
 * Which entries of a refused save the server named, so autosave can set those aside.
 *
 * A save is one batch and one transaction, refused whole. Before the server named the
 * entry a refusal came from, the only thing autosave could do with a refused batch was put
 * all of it back and send it again — and a batch holding one object the server will never
 * accept is refused every time, so every edit made after that object was lost with it.
 *
 * The server names an entry the way request validation names a field (`docs/API.md`):
 * `["body", "created_shapes", 1]`, or `["body", "created_shapes", 1, "points"]` when the
 * request itself was malformed there. Either way the entry is what gets set aside.
 */

import type { ProblemDocument } from '@/api/types';

/** The lists of a batch that autosave sends one object per entry of. */
export type RefusableList = 'created_shapes' | 'updated_shapes' | 'updated_tracks';

const REFUSABLE: readonly string[] = ['created_shapes', 'updated_shapes', 'updated_tracks'];

export interface RefusedEntry {
  list: RefusableList;
  index: number;
  /** The server's own reason, with the field it concerns when it named one. */
  reason: string;
}

export function refusedEntries(problem: ProblemDocument): RefusedEntry[] {
  const refusals: RefusedEntry[] = [];
  for (const error of problem.errors ?? []) {
    const [where, list, index, ...field] = error.location;
    if (where !== 'body' || typeof list !== 'string' || !REFUSABLE.includes(list)) continue;
    if (typeof index !== 'number') continue;
    refusals.push({
      list: list as RefusableList,
      index,
      reason: field.length > 0 ? `${field.join('.')}: ${error.message}` : error.message,
    });
  }
  return refusals;
}
