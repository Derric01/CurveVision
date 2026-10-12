/**
 * Renaming and deleting a project or a task, from its page.
 *
 * `PATCH` and `DELETE` on both existed and the app offered neither: a misnamed task stayed
 * misnamed and a finished experiment stayed in the list unless someone used the API.
 * These are the rules the control shows. Who may act mirrors the policy engine (renaming
 * either takes maintainer; deleting a task takes maintainer, a project admin). Deleting is
 * confirmed by typing the name — CVAT asks only "are you sure", and one stray click on the
 * wrong row of a list of similar names then destroys someone's month.
 */

import type { Role } from '@/api/types';

export type Resource = 'project' | 'task';

const RANK: readonly Role[] = ['viewer', 'annotator', 'reviewer', 'maintainer', 'admin', 'owner'];

function atLeast(role: Role | undefined, floor: Role): boolean {
  // `undefined`: a server too old to report the caller's role, whose 403 then answers.
  return role === undefined || RANK.indexOf(role) >= RANK.indexOf(floor);
}

export function canRename(role: Role | undefined): boolean {
  return atLeast(role, 'maintainer');
}

export function canDelete(resource: Resource, role: Role | undefined): boolean {
  return atLeast(role, resource === 'project' ? 'admin' : 'maintainer');
}

/** The name as it will be saved, or null when it is no change or not a name. */
export function renamed(current: string, draft: string, maxLength: number): string | null {
  const name = draft.trim().replace(/\s+/g, ' ');
  if (!name || name === current || name.length > maxLength) return null;
  return name;
}

export const MAX_NAME: Record<Resource, number> = { project: 200, task: 300 };

/** Whether what was typed is the name — exactly, ignoring surrounding space. */
export function deleteConfirmed(name: string, typed: string): boolean {
  return typed.trim() === name.trim() && name.trim().length > 0;
}

/** What the delete takes with it, said before it is done. */
export function deleteConsequence(
  resource: Resource,
  counts: { tasks?: number; frames?: number },
): string {
  const plural = (n: number, one: string) => `${n.toLocaleString()} ${one}${n === 1 ? '' : 's'}`;
  const what =
    resource === 'project'
      ? `its ${plural(counts.tasks ?? 0, 'task')}, their jobs, annotations, reviews and ` +
        'dataset versions'
      : `its ${plural(counts.frames ?? 0, 'frame')}, jobs, annotations and reviews`;
  return (
    `Deleting this ${resource} deletes ${what}. Its images and videos are removed from ` +
    'storage unless another task uses them. This cannot be undone.'
  );
}
